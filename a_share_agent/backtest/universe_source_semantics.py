from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file

SEMANTICS_FILE = "source_semantics.json"
SSE_VERIFICATION_FILE = "sse_delisting_date_verification.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def load_source_semantics(raw_register_dir: Path) -> dict[str, Any]:
    path = raw_register_dir / SEMANTICS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def validate_universe_source_semantics(raw_register_dir: Path) -> dict[str, Any]:
    """Validate whether official-register date fields are safe for PIT boundaries.

    A source may have matching verification samples while still being rejected for
    source-wide use.  This intentionally distinguishes *sample evidence* from a
    complete semantic contract.  Snapshot generation/readiness must not promote an
    ambiguous field simply because a handful of rows happen to match announcements.
    """
    semantics = load_source_semantics(raw_register_dir)
    sources = semantics.get("sources") if isinstance(semantics, dict) else None
    if not isinstance(sources, dict):
        sources = {}

    required = ("szse_delisted_register.csv", "sse_delisted_register.csv")
    missing_entries = [name for name in required if name not in sources]
    invalid_entries: list[str] = []
    source_results: dict[str, Any] = {}

    for name in required:
        entry = sources.get(name)
        if not isinstance(entry, dict):
            continue
        raw_field = str(entry.get("raw_date_field") or "").strip()
        normalized = str(entry.get("normalized_field") or "").strip()
        status = str(entry.get("semantic_status") or "").strip()
        allow = entry.get("allow_as_delisting_date") is True
        valid = bool(raw_field and normalized == "delisting_date" and status)
        if not valid:
            invalid_entries.append(name)
        source_results[name] = {
            "raw_date_field": raw_field,
            "normalized_field": normalized,
            "semantic_status": status,
            "allow_as_delisting_date": allow,
            "entry_valid": valid,
            "reason": str(entry.get("reason") or ""),
        }

    verification_path = raw_register_dir / SSE_VERIFICATION_FILE
    verification_rows = _read_csv(verification_path)
    verification_mismatches = []
    verification_missing_fields = []
    for idx, row in enumerate(verification_rows, start=2):
        symbol = str(row.get("symbol") or "").strip()
        raw_date = str(row.get("raw_date") or "").strip()
        verified_date = str(row.get("verified_delisting_date") or "").strip()
        status = str(row.get("verification_status") or "").strip().upper()
        url = str(row.get("official_announcement_url") or "").strip()
        if not symbol or not raw_date or not verified_date or not url:
            verification_missing_fields.append(idx)
            continue
        if status != "MATCH" or raw_date != verified_date:
            verification_mismatches.append(symbol)

    sse = source_results.get("sse_delisted_register.csv", {})
    szse = source_results.get("szse_delisted_register.csv", {})
    sample_evidence_valid = bool(
        verification_rows
        and not verification_mismatches
        and not verification_missing_fields
    )
    source_wide_verified = bool(
        not missing_entries
        and not invalid_entries
        and szse.get("allow_as_delisting_date") is True
        and sse.get("allow_as_delisting_date") is True
    )

    return {
        "manifest_present": bool(semantics),
        "manifest_path": str(raw_register_dir / SEMANTICS_FILE),
        "manifest_sha256": sha256_file(raw_register_dir / SEMANTICS_FILE) if (raw_register_dir / SEMANTICS_FILE).exists() else None,
        "required_sources": list(required),
        "missing_entries": missing_entries,
        "invalid_entries": invalid_entries,
        "sources": source_results,
        "sse_verification_file_present": verification_path.exists(),
        "sse_verification_file_sha256": sha256_file(verification_path) if verification_path.exists() else None,
        "sse_verification_sample_count": len(verification_rows),
        "sse_verification_mismatches": verification_mismatches,
        "sse_verification_missing_fields": verification_missing_fields,
        "sse_sample_evidence_valid": sample_evidence_valid,
        "source_wide_verified": source_wide_verified,
        "ready": source_wide_verified,
        "reason": (
            "OK"
            if source_wide_verified
            else "SSE_DELISTING_DATE_FIELD_SEMANTICS_NOT_SOURCE_WIDE_VERIFIED"
        ),
    }


def require_delisting_field_semantics(raw_register_dir: Path, source_file: str) -> dict[str, Any]:
    """Return the declared mapping or raise when it is unsafe for snapshot generation."""
    semantics = load_source_semantics(raw_register_dir)
    sources = semantics.get("sources") if isinstance(semantics, dict) else None
    entry = sources.get(source_file) if isinstance(sources, dict) else None
    if not isinstance(entry, dict):
        raise RuntimeError(f"missing source semantics entry for {source_file}")
    if entry.get("allow_as_delisting_date") is not True:
        raise RuntimeError(
            f"unverified delisting-date semantics for {source_file}: "
            f"{entry.get('semantic_status') or 'UNKNOWN'}"
        )
    raw_field = str(entry.get("raw_date_field") or "").strip()
    if not raw_field:
        raise RuntimeError(f"missing raw_date_field semantics for {source_file}")
    return entry
