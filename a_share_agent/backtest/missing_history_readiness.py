from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _as_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _valid_sha256(value: str) -> bool:
    value = str(value or "").strip().lower()
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _valid_source_ids(sidecar_path: Path) -> set[str]:
    valid: set[str] = set()
    for row in _read_csv(sidecar_path):
        source_id = str(row.get("source_id") or row.get("source") or "").strip()
        document = str(
            row.get("source_document_id_or_url")
            or row.get("source_url_or_document_id")
            or row.get("source_record_id")
            or row.get("document_id")
            or row.get("url")
            or ""
        ).strip()
        digest = str(
            row.get("raw_source_hash")
            or row.get("source_file_sha256")
            or row.get("source_sha256")
            or ""
        ).strip().lower()
        if source_id and document and _valid_sha256(digest):
            valid.add(source_id)
    return valid


def _symbol_intervals_verified(interval_path: Path, sidecar_path: Path, symbol: str) -> tuple[bool, int]:
    rows = [row for row in _read_csv(interval_path) if str(row.get("symbol") or "").strip() == symbol]
    if not rows:
        return False, 0
    valid_sources = _valid_source_ids(sidecar_path)
    if not valid_sources:
        return False, len(rows)
    return all(str(row.get("source") or "").strip() in valid_sources for row in rows), len(rows)


def _raw_file_status(root: Path, symbol: str) -> dict[str, Any]:
    filename = symbol.replace(".", "_") + ".csv"
    raw_path = root / "data" / "backtest" / "raw_prices" / filename
    manifest_path = root / "raw_dataset_manifest.json"
    manifest: dict[str, Any] = {}
    try:
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    file_hashes = manifest.get("file_hashes") if isinstance(manifest, dict) else None
    if not isinstance(file_hashes, dict):
        file_hashes = {}
    expected = str(file_hashes.get(filename) or "").strip().lower()
    exists = raw_path.exists()
    actual = sha256_file(raw_path) if exists else None
    verified = bool(exists and _valid_sha256(expected) and actual == expected)
    return {
        "raw_filename": filename,
        "raw_file_exists": exists,
        "raw_manifest_hash_present": _valid_sha256(expected),
        "raw_file_hash_match": verified,
        "expected_raw_file_hash": expected or None,
        "actual_raw_file_hash": actual,
    }


def assess_missing_history_candidate(root: Path, candidate: dict[str, str]) -> dict[str, Any]:
    """Assess two distinct readiness levels for one missing-history security.

    ``membership_ready`` means the independent reference data is sufficient to model
    PIT membership boundaries. It does *not* imply the strategy may trade the symbol.

    ``execution_ready`` additionally requires a Raw bar file bound to the current
    dataset manifest and verified historical Status/Sector provenance. This function
    never mutates ``security_master.csv`` or any historical dataset.
    """
    symbol = str(candidate.get("symbol") or "").strip()
    listing_date = str(candidate.get("listing_date") or "").strip()
    delisting_date = str(candidate.get("raw_delisting_date") or "").strip()
    raw_record_found = _as_true(candidate.get("raw_record_found"))
    source_allows = _as_true(candidate.get("source_allows_delisting_date"))
    candidate_status = str(candidate.get("candidate_status") or "").strip()

    membership_ready = bool(
        symbol
        and listing_date
        and delisting_date
        and raw_record_found
        and source_allows
        and candidate_status.startswith("DATE_PROVENANCE_READY")
    )

    raw = _raw_file_status(root, symbol) if symbol else {
        "raw_filename": None,
        "raw_file_exists": False,
        "raw_manifest_hash_present": False,
        "raw_file_hash_match": False,
        "expected_raw_file_hash": None,
        "actual_raw_file_hash": None,
    }
    status_verified, status_interval_count = _symbol_intervals_verified(
        root / "data" / "backtest" / "historical_status_intervals.csv",
        root / "data" / "backtest" / "status_provenance.csv",
        symbol,
    )
    sector_verified, sector_interval_count = _symbol_intervals_verified(
        root / "data" / "backtest" / "historical_sector_intervals.csv",
        root / "data" / "backtest" / "sector_provenance.csv",
        symbol,
    )

    execution_ready = bool(
        membership_ready
        and raw["raw_file_hash_match"]
        and status_verified
        and sector_verified
    )

    blockers: list[str] = []
    if not membership_ready:
        blockers.append("PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE")
    if not raw["raw_file_hash_match"]:
        blockers.append("RAW_OHLCV_NOT_MANIFEST_VERIFIED")
    if not status_verified:
        blockers.append("HISTORICAL_STATUS_PROVENANCE_INCOMPLETE")
    if not sector_verified:
        blockers.append("HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE")

    return {
        "symbol": symbol,
        "membership_ready": membership_ready,
        "execution_ready": execution_ready,
        "listing_date": listing_date,
        "delisting_date": delisting_date,
        "candidate_status": candidate_status,
        "status_interval_count": status_interval_count,
        "status_provenance_verified": status_verified,
        "sector_interval_count": sector_interval_count,
        "sector_provenance_verified": sector_verified,
        "blockers": blockers,
        **raw,
    }


def audit_missing_history_candidates(root: Path, candidates_path: Path | None = None) -> dict[str, Any]:
    candidates_path = candidates_path or (root / "security_master_missing_history_candidates.csv")
    candidates = _read_csv(candidates_path)
    rows = [assess_missing_history_candidate(root, row) for row in candidates]
    return {
        "candidate_count": len(rows),
        "membership_ready_count": sum(1 for row in rows if row["membership_ready"]),
        "execution_ready_count": sum(1 for row in rows if row["execution_ready"]),
        "blocked_membership_count": sum(1 for row in rows if not row["membership_ready"]),
        "blocked_execution_count": sum(1 for row in rows if not row["execution_ready"]),
        "rows": rows,
    }
