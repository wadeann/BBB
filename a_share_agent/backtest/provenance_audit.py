from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .data_integrity import sha256_file

CA_KEY_FIELDS = ("symbol", "action_type", "ex_date", "record_date")
CA_VALUE_FIELDS = (
    "cash_dividend_per_share",
    "bonus_ratio",
    "stock_dividend_ratio",
    "split_ratio",
    "rights_ratio",
    "rights_price",
)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _row_document_id(row: dict[str, str]) -> str:
    for key in ("source_document_id_or_url", "source_url_or_document_id", "source_record_id", "document_id", "url"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_raw_source_hash(row: dict[str, str]) -> str:
    for key in ("raw_source_hash", "source_file_sha256", "source_sha256"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _row_raw_source_path(row: dict[str, Any]) -> str:
    for key in ("raw_source_path", "source_file_path", "source_path", "raw_source_file", "path", "file"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _resolve_source_artifact(anchor_dir: Path, declared_path: str) -> Path | None:
    raw = str(declared_path or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (anchor_dir / p)


def _verify_source_artifact(anchor_dir: Path, row: dict[str, Any], digest: str) -> dict[str, Any]:
    declared_path = _row_raw_source_path(row)
    artifact = _resolve_source_artifact(anchor_dir, declared_path)
    exists = bool(artifact and artifact.exists() and artifact.is_file())
    actual = sha256_file(artifact) if exists and artifact is not None else None
    match = bool(exists and _valid_sha256(digest) and actual == digest.lower())
    return {
        "declared_path": declared_path or None,
        "resolved_path": str(artifact) if artifact is not None else None,
        "exists": exists,
        "actual_sha256": actual,
        "hash_match": match,
    }


def validate_official_ca_register(register_path: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate an independently sourced Corporate Action register fail-closed.

    In addition to binding the normalized register itself, every manifest-declared raw
    source file must be physically available and re-hash to its declared SHA256. Every
    event row must then bind to one of those *verified* raw-source hashes plus a concrete
    document/record identifier. A syntactically plausible SHA string is never enough.
    """
    result: dict[str, Any] = {
        "valid": False,
        "reason": "OFFICIAL_CA_REGISTER_OR_MANIFEST_MISSING",
        "source_dataset_id": None,
        "event_count": 0,
        "duplicate_event_keys": 0,
        "rows_missing_provenance": 0,
        "rows_with_unknown_source_hash": 0,
        "manifest_event_count_match": False,
        "source_files_valid": False,
        "source_files_missing_artifact": 0,
        "source_files_hash_mismatch": 0,
        "source_files_invalid_declaration": 0,
    }
    if not register_path.exists() or not manifest_path.exists():
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        result["reason"] = "OFFICIAL_CA_MANIFEST_INVALID_JSON"
        return result

    rows = read_csv_rows(register_path)
    source_files = manifest.get("source_files") or manifest.get("raw_source_files") or []
    if not isinstance(source_files, list):
        source_files = []

    verified_sources: list[dict[str, str]] = []
    invalid_declaration = 0
    missing_artifact = 0
    hash_mismatch = 0
    for item in source_files:
        if not isinstance(item, dict):
            invalid_declaration += 1
            continue
        source_id = str(item.get("source_id") or item.get("name") or item.get("document_id") or item.get("file") or "").strip()
        digest = str(item.get("sha256") or item.get("raw_source_hash") or "").strip().lower()
        declared_path = _row_raw_source_path(item)
        if not source_id or not _valid_sha256(digest) or not declared_path:
            invalid_declaration += 1
            continue
        artifact_status = _verify_source_artifact(manifest_path.parent, item, digest)
        if not artifact_status["exists"]:
            missing_artifact += 1
            continue
        if not artifact_status["hash_match"]:
            hash_mismatch += 1
            continue
        verified_sources.append({"source_id": source_id, "sha256": digest})
    source_hashes = {x["sha256"] for x in verified_sources}

    keys = [tuple(str(r.get(k) or "").strip() for k in CA_KEY_FIELDS) for r in rows]
    duplicate_count = sum(v - 1 for v in Counter(keys).values() if v > 1)
    malformed_key_rows = sum(1 for k in keys if not all(k))
    missing_provenance = 0
    unknown_hash = 0
    for row in rows:
        source = str(row.get("source") or "").strip()
        document_id = _row_document_id(row)
        raw_hash = _row_raw_source_hash(row)
        if not source or not document_id or not _valid_sha256(raw_hash):
            missing_provenance += 1
        elif raw_hash not in source_hashes:
            unknown_hash += 1

    expected_register_hash = str(manifest.get("register_sha256") or "").strip().lower()
    actual_register_hash = sha256_file(register_path)
    source_type = str(manifest.get("source_type") or "").strip()
    source_dataset_id = str(manifest.get("source_dataset_id") or "").strip()
    manifest_count = manifest.get("event_count")
    try:
        manifest_count_int = int(manifest_count)
    except Exception:
        manifest_count_int = -1

    event_count_match = manifest_count_int == len(rows)
    source_files_valid = bool(
        source_files
        and len(verified_sources) == len(source_files)
        and invalid_declaration == 0
        and missing_artifact == 0
        and hash_mismatch == 0
    )
    valid = bool(
        source_type == "INDEPENDENT_OFFICIAL_EXPORT"
        and source_dataset_id
        and expected_register_hash
        and expected_register_hash == actual_register_hash
        and rows
        and source_files_valid
        and event_count_match
        and malformed_key_rows == 0
        and duplicate_count == 0
        and missing_provenance == 0
        and unknown_hash == 0
    )
    result.update(
        {
            "valid": valid,
            "reason": "OK" if valid else "OFFICIAL_CA_PROVENANCE_OR_HASH_INVALID",
            "source_dataset_id": source_dataset_id or None,
            "event_count": len(rows),
            "duplicate_event_keys": duplicate_count,
            "malformed_event_keys": malformed_key_rows,
            "rows_missing_provenance": missing_provenance,
            "rows_with_unknown_source_hash": unknown_hash,
            "manifest_event_count_match": event_count_match,
            "source_files_valid": source_files_valid,
            "source_files_missing_artifact": missing_artifact,
            "source_files_hash_mismatch": hash_mismatch,
            "source_files_invalid_declaration": invalid_declaration,
            "verified_source_file_count": len(verified_sources),
            "expected_register_hash": expected_register_hash or None,
            "actual_register_hash": actual_register_hash,
        }
    )
    return result


def corporate_action_event_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(k) or "").strip() for k in CA_KEY_FIELDS)  # type: ignore[return-value]


def corporate_action_values(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(str(row.get(k) or "").strip() for k in CA_VALUE_FIELDS)


def reconcile_corporate_action_sets(prod_path: Path, register_path: Path, manifest_path: Path) -> dict[str, Any]:
    status = validate_official_ca_register(register_path, manifest_path)
    prod_rows = read_csv_rows(prod_path)
    synthetic = sum(
        1
        for row in prod_rows
        if "SYNTHETIC" in str(row.get("source") or "").upper()
        or "SYNTHETIC" in _row_document_id(row).upper()
    )
    if not status["valid"]:
        return {
            "official_register_valid": False,
            "official_register_validation": status,
            "official_source_dataset_id": status.get("source_dataset_id"),
            "expected_events": 0,
            "loaded_events": len(prod_rows),
            "matched_events": 0,
            "missing_events": 0,
            "extra_events": 0,
            "conflicting_events": 0,
            "synthetic_events": synthetic,
            "ratio": 0.0,
            "complete": False,
        }

    official_rows = read_csv_rows(register_path)
    prod_map = {corporate_action_event_key(r): r for r in prod_rows if all(corporate_action_event_key(r))}
    off_map = {corporate_action_event_key(r): r for r in official_rows if all(corporate_action_event_key(r))}
    expected, loaded = set(off_map), set(prod_map)
    missing, extra, common = expected - loaded, loaded - expected, expected & loaded
    conflicts = {k for k in common if corporate_action_values(off_map[k]) != corporate_action_values(prod_map[k])}
    matched = len(common) - len(conflicts)
    complete = bool(expected and not missing and not extra and not conflicts and not synthetic and len(prod_map) == len(off_map))
    return {
        "official_register_valid": True,
        "official_register_validation": status,
        "official_source_dataset_id": status.get("source_dataset_id"),
        "expected_events": len(expected),
        "loaded_events": len(loaded),
        "matched_events": matched,
        "missing_events": len(missing),
        "extra_events": len(extra),
        "conflicting_events": len(conflicts),
        "synthetic_events": synthetic,
        "ratio": round(matched / len(expected), 4) if expected else 0.0,
        "complete": complete,
        "missing_keys": sorted(missing),
        "extra_keys": sorted(extra),
        "conflicting_keys": sorted(conflicts),
    }


def audit_interval_provenance(
    interval_path: Path,
    provenance_path: Path,
    *,
    min_coverage: float = 0.95,
) -> dict[str, Any]:
    """Audit PIT interval provenance using physically verified raw-source artifacts.

    Each sidecar row must include source_id, a concrete document/url, a raw-source
    SHA256, and a path to the corresponding raw artifact. The artifact is re-hashed at
    audit time. Generic labels or syntactically plausible hashes do not count.
    """
    intervals = read_csv_rows(interval_path)
    sidecar = read_csv_rows(provenance_path)
    valid_sources: dict[str, dict[str, str]] = {}
    invalid_sidecar_rows = 0
    missing_source_artifacts = 0
    source_artifact_hash_mismatches = 0
    for row in sidecar:
        source_id = str(row.get("source_id") or row.get("source") or "").strip()
        document_id = _row_document_id(row)
        raw_hash = _row_raw_source_hash(row)
        declared_path = _row_raw_source_path(row)
        if not source_id or not document_id or not _valid_sha256(raw_hash) or not declared_path:
            invalid_sidecar_rows += 1
            continue
        artifact_status = _verify_source_artifact(provenance_path.parent, row, raw_hash)
        if not artifact_status["exists"]:
            missing_source_artifacts += 1
            continue
        if not artifact_status["hash_match"]:
            source_artifact_hash_mismatches += 1
            continue
        valid_sources[source_id] = row

    total = len(intervals)
    verified = 0
    source_counts: Counter[str] = Counter()
    unverified_counts: Counter[str] = Counter()
    for row in intervals:
        source_id = str(row.get("source") or "").strip()
        source_counts[source_id] += 1
        if source_id and source_id in valid_sources:
            verified += 1
        else:
            unverified_counts[source_id or "<EMPTY_SOURCE>"] += 1
    coverage = verified / total if total else 0.0
    dataset_complete = bool(
        total
        and coverage >= min_coverage
        and invalid_sidecar_rows == 0
        and missing_source_artifacts == 0
        and source_artifact_hash_mismatches == 0
    )
    return {
        "interval_file": str(interval_path),
        "provenance_file": str(provenance_path),
        "provenance_sidecar_present": provenance_path.exists(),
        "interval_count": total,
        "verified_interval_count": verified,
        "source_coverage": round(coverage, 4),
        "dataset_complete": dataset_complete,
        "min_coverage": min_coverage,
        "valid_source_ids": len(valid_sources),
        "invalid_sidecar_rows": invalid_sidecar_rows,
        "missing_source_artifacts": missing_source_artifacts,
        "source_artifact_hash_mismatches": source_artifact_hash_mismatches,
        "unverified_source_ids": sorted(unverified_counts),
        "unverified_interval_count_by_source": dict(sorted(unverified_counts.items())),
        "interval_count_by_source": dict(sorted(source_counts.items())),
    }


def write_source_audit_csv(path: Path, audit: dict[str, Any]) -> None:
    rows = []
    all_counts = audit.get("interval_count_by_source") or {}
    missing_counts = audit.get("unverified_interval_count_by_source") or {}
    for source_id, count in sorted(all_counts.items()):
        rows.append(
            {
                "source_id": source_id,
                "interval_count": count,
                "verified_provenance": str(source_id not in missing_counts),
                "unverified_interval_count": missing_counts.get(source_id, 0),
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["source_id", "interval_count", "verified_provenance", "unverified_interval_count"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def is_a_share_common_equity_symbol(symbol: str, board: str) -> bool:
    code = symbol.split(".", 1)[0].zfill(6)
    board = board.upper()
    if board == "SSE_MAIN":
        return code.startswith(("600", "601", "603", "605"))
    if board == "STAR":
        return code.startswith("688")
    if board == "SZSE_MAIN":
        return code.startswith(("000", "001", "002", "003"))
    if board == "CHINEXT":
        return code.startswith(("300", "301"))
    if board == "BSE":
        return code.startswith(("43", "83", "87", "88", "92"))
    return False


def local_record_active_on(row: dict[str, str], audit_date: str) -> bool:
    start = str(row.get("active_from") or row.get("listing_date") or "").strip()
    end = str(row.get("active_to") or row.get("delisting_date") or "").strip()
    if start and audit_date < start:
        return False
    if end and audit_date > end:
        return False
    return True


def classify_universe_difference(
    *,
    difference_type: str,
    audit_date: str,
    official_row: dict[str, str] | None,
    local_rows: Iterable[dict[str, str]],
    excluded_security_type: str | None = None,
) -> str:
    rows = list(local_rows)
    if excluded_security_type and excluded_security_type != "A_SHARE_COMMON_EQUITY":
        return "OFFICIAL_SOURCE_NON_A_SHARE"
    if difference_type == "MISSING_IN_LOCAL":
        if not rows:
            return "LOCAL_MASTER_MISSING"
        if any(str(r.get("listing_date") or r.get("active_from") or "") > audit_date for r in rows):
            return "LOCAL_LISTING_DATE_MISMATCH"
        if any((str(r.get("active_to") or r.get("delisting_date") or "") and str(r.get("active_to") or r.get("delisting_date") or "") < audit_date) for r in rows):
            return "LOCAL_DELISTING_DATE_MISMATCH"
        return "SNAPSHOT_DATE_SEMANTICS"
    if difference_type == "EXTRA_IN_LOCAL":
        if not rows:
            return "LOCAL_SET_CONSTRUCTION_ERROR"
        if any(str(r.get("listing_date") or r.get("active_from") or "") > audit_date for r in rows):
            return "LOCAL_PRELISTING_LEAKAGE"
        if any((str(r.get("active_to") or r.get("delisting_date") or "") and str(r.get("active_to") or r.get("delisting_date") or "") < audit_date) for r in rows):
            return "LOCAL_POST_DELISTING_LEAKAGE"
        return "OFFICIAL_SNAPSHOT_MISSING_OR_DATE_SEMANTICS"
    return "UNCLASSIFIED"
