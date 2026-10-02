from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _valid_sha256(value: str) -> bool:
    value = str(value or "").strip().lower()
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _resolve_inside(root: Path, value: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        base = root.resolve()
    except Exception:
        return None
    if resolved != base and base not in resolved.parents:
        return None
    return resolved


def _transform_audit(root: Path, normalization: dict[str, Any]) -> dict[str, Any]:
    mode = str(normalization.get("mode") or "").strip().upper()
    if mode == "DIRECT_SOURCE_EXPORT":
        return {"verified": True, "mode": mode, "reason": "DIRECT_SOURCE_EXPORT"}
    if mode != "DETERMINISTIC_TRANSFORM":
        return {"verified": False, "mode": mode or None, "reason": "NORMALIZATION_MODE_INVALID"}
    raw_path = str(normalization.get("transformer_path") or "").strip()
    expected = str(normalization.get("transformer_sha256") or "").strip().lower()
    path = _resolve_inside(root, raw_path)
    exists = bool(path and path.is_file())
    actual = sha256_file(path) if exists and path is not None else None
    verified = bool(exists and _valid_sha256(expected) and actual == expected)
    return {
        "verified": verified,
        "mode": mode,
        "transformer_path": raw_path or None,
        "expected_transformer_sha256": expected or None,
        "actual_transformer_sha256": actual,
        "reason": "OK" if verified else "TRANSFORMER_MISSING_OR_HASH_MISMATCH",
    }


def audit_raw_price_provenance(
    root: Path,
    *,
    actual_dataset_hash: str | None,
    research_start: str,
    research_end: str,
) -> dict[str, Any]:
    """Verify independent origin evidence for every normalized Raw OHLCV CSV.

    Dataset integrity and dataset authenticity are deliberately separate. The ordinary
    ``raw_dataset_manifest.json`` proves only that mounted normalized CSV bytes match a
    fingerprint. Formal research additionally requires this provenance contract:

    - ``data/backtest/raw_price_provenance_manifest.json`` binds the normalized dataset
      hash, its integrity manifest, the row-level provenance CSV and normalization code;
    - ``data/backtest/raw_price_provenance.csv`` has exactly one row per normalized Raw
      file and binds that file hash to an independent source artifact, source id,
      document/URL and source record id;
    - each referenced source artifact physically exists and re-hashes to the declared
      SHA256. Reusing one source archive for many securities is allowed, but each row
      must identify its source record within that archive/export.
    """
    manifest_path = root / "data" / "backtest" / "raw_price_provenance_manifest.json"
    provenance_path = root / "data" / "backtest" / "raw_price_provenance.csv"
    raw_manifest_path = root / "raw_dataset_manifest.json"
    raw_manifest = _read_json(raw_manifest_path)
    expected_files = raw_manifest.get("file_hashes") if isinstance(raw_manifest, dict) else None
    if not isinstance(expected_files, dict):
        expected_files = {}

    base: dict[str, Any] = {
        "verified": False,
        "reason": "RAW_PRICE_PROVENANCE_MANIFEST_OR_CSV_MISSING",
        "manifest_present": manifest_path.exists(),
        "provenance_csv_present": provenance_path.exists(),
        "expected_normalized_file_count": len(expected_files),
        "provenance_row_count": 0,
        "missing_provenance_files": sorted(expected_files),
        "extra_provenance_files": [],
        "duplicate_provenance_files": [],
        "normalized_hash_mismatches": [],
        "invalid_provenance_rows": 0,
        "missing_source_artifacts": 0,
        "source_artifact_hash_mismatches": 0,
        "normalization_verified": False,
    }
    if not manifest_path.exists() or not provenance_path.exists() or not raw_manifest_path.exists():
        return base

    manifest = _read_json(manifest_path)
    rows = _read_csv(provenance_path)
    source_type = str(manifest.get("source_type") or "").strip()
    source_dataset_id = str(manifest.get("source_dataset_id") or "").strip()
    declared_dataset_hash = str(manifest.get("normalized_dataset_hash") or "").strip().lower()
    expected_raw_manifest_hash = str(manifest.get("raw_dataset_manifest_sha256") or "").strip().lower()
    expected_prov_hash = str(manifest.get("provenance_csv_sha256") or "").strip().lower()
    actual_raw_manifest_hash = sha256_file(raw_manifest_path)
    actual_prov_hash = sha256_file(provenance_path)
    date_range = manifest.get("date_range") if isinstance(manifest.get("date_range"), dict) else {}
    source_start = str(date_range.get("start") or "").strip()
    source_end = str(date_range.get("end") or "").strip()
    date_range_covers = bool(source_start and source_end and source_start <= research_start and source_end >= research_end)
    normalization = manifest.get("normalization") if isinstance(manifest.get("normalization"), dict) else {}
    transform = _transform_audit(root, normalization)

    raw_names = [str(row.get("raw_filename") or "").strip() for row in rows]
    counts = Counter(name for name in raw_names if name)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    row_by_file = {str(row.get("raw_filename") or "").strip(): row for row in rows if str(row.get("raw_filename") or "").strip()}
    expected_names = set(str(name) for name in expected_files)
    actual_names = set(row_by_file)
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)

    normalized_mismatches: list[str] = []
    invalid_rows = 0
    source_missing = 0
    source_hash_mismatch = 0
    artifact_cache: dict[tuple[str, str], tuple[bool, str | None]] = {}

    for filename, row in row_by_file.items():
        normalized_hash = str(row.get("normalized_sha256") or "").strip().lower()
        expected_normalized = str(expected_files.get(filename) or "").strip().lower()
        if not _valid_sha256(normalized_hash) or normalized_hash != expected_normalized:
            normalized_mismatches.append(filename)

        source_id = str(row.get("source_id") or "").strip()
        document = str(row.get("source_document_id_or_url") or row.get("source_url_or_document_id") or "").strip()
        record_id = str(row.get("source_record_id") or row.get("source_row_id") or row.get("source_symbol") or "").strip()
        artifact_path = str(row.get("source_artifact_path") or row.get("raw_source_path") or "").strip()
        artifact_hash = str(row.get("source_artifact_sha256") or row.get("raw_source_hash") or "").strip().lower()
        if not source_id or not document or not record_id or not artifact_path or not _valid_sha256(artifact_hash):
            invalid_rows += 1
            continue

        key = (artifact_path, artifact_hash)
        cached = artifact_cache.get(key)
        if cached is None:
            resolved = _resolve_inside(root, artifact_path)
            exists = bool(resolved and resolved.is_file())
            actual_hash = sha256_file(resolved) if exists and resolved is not None else None
            cached = (exists, actual_hash)
            artifact_cache[key] = cached
        exists, actual_hash = cached
        if not exists:
            source_missing += 1
        elif actual_hash != artifact_hash:
            source_hash_mismatch += 1

    manifest_row_count = manifest.get("provenance_row_count")
    try:
        manifest_row_count_int = int(manifest_row_count)
    except Exception:
        manifest_row_count_int = -1
    row_count_match = manifest_row_count_int == len(rows)

    verified = bool(
        source_type == "INDEPENDENT_MARKET_DATA_EXPORT"
        and source_dataset_id
        and actual_dataset_hash
        and declared_dataset_hash == str(actual_dataset_hash).lower()
        and _valid_sha256(expected_raw_manifest_hash)
        and expected_raw_manifest_hash == actual_raw_manifest_hash
        and _valid_sha256(expected_prov_hash)
        and expected_prov_hash == actual_prov_hash
        and date_range_covers
        and transform["verified"]
        and expected_names
        and rows
        and row_count_match
        and not missing
        and not extra
        and not duplicates
        and not normalized_mismatches
        and invalid_rows == 0
        and source_missing == 0
        and source_hash_mismatch == 0
    )

    base.update(
        {
            "verified": verified,
            "reason": "OK" if verified else "RAW_PRICE_PROVENANCE_INCOMPLETE_OR_INVALID",
            "source_type": source_type or None,
            "source_dataset_id": source_dataset_id or None,
            "declared_normalized_dataset_hash": declared_dataset_hash or None,
            "actual_normalized_dataset_hash": actual_dataset_hash,
            "raw_dataset_manifest_hash_match": bool(expected_raw_manifest_hash and expected_raw_manifest_hash == actual_raw_manifest_hash),
            "provenance_csv_hash_match": bool(expected_prov_hash and expected_prov_hash == actual_prov_hash),
            "date_range": {"start": source_start or None, "end": source_end or None},
            "date_range_covers_research": date_range_covers,
            "provenance_row_count": len(rows),
            "manifest_provenance_row_count": manifest_row_count_int,
            "provenance_row_count_match": row_count_match,
            "missing_provenance_files": missing,
            "extra_provenance_files": extra,
            "duplicate_provenance_files": duplicates,
            "normalized_hash_mismatches": sorted(normalized_mismatches),
            "invalid_provenance_rows": invalid_rows,
            "unique_source_artifacts": len(artifact_cache),
            "missing_source_artifacts": source_missing,
            "source_artifact_hash_mismatches": source_hash_mismatch,
            "normalization_verified": bool(transform["verified"]),
            "normalization_audit": transform,
        }
    )
    return base
