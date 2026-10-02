from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file
from .trusted_research_sources import verify_source_artifact

SEMANTICS_FILE = "source_semantics.json"
SSE_VERIFICATION_FILE = "sse_delisting_date_verification.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _project_root_for(raw_register_dir: Path) -> Path:
    if (
        raw_register_dir.name == "raw_registers"
        and raw_register_dir.parent.name == "official_universe_snapshots"
        and raw_register_dir.parent.parent.name == "backtest"
        and raw_register_dir.parent.parent.parent.name == "data"
    ):
        return raw_register_dir.parent.parent.parent.parent
    return raw_register_dir.parent


def load_source_semantics(raw_register_dir: Path) -> dict[str, Any]:
    return _read_json(raw_register_dir / SEMANTICS_FILE)


def _binding_audit(raw_register_dir: Path) -> dict[str, Any]:
    snapshot_dir = raw_register_dir.parent
    manifest = _read_json(snapshot_dir / "manifest.json")
    binding = manifest.get("source_semantics") if isinstance(manifest, dict) else None
    if not isinstance(binding, dict):
        binding = {}
    semantics_path = raw_register_dir / SEMANTICS_FILE
    verification_path = raw_register_dir / SSE_VERIFICATION_FILE
    expected_semantics_hash = str(binding.get("sha256") or binding.get("semantics_sha256") or "").strip().lower()
    expected_verification_hash = str(binding.get("sse_verification_sha256") or "").strip().lower()
    actual_semantics_hash = sha256_file(semantics_path) if semantics_path.exists() else None
    actual_verification_hash = sha256_file(verification_path) if verification_path.exists() else None
    declared_file = str(binding.get("file") or "").strip()
    return {
        "manifest_binding_present": bool(binding),
        "declared_file": declared_file or None,
        "semantics_hash_match": bool(
            semantics_path.exists()
            and declared_file
            and Path(declared_file).name == semantics_path.name
            and expected_semantics_hash
            and actual_semantics_hash == expected_semantics_hash
        ),
        "verification_hash_match": bool(
            verification_path.exists()
            and expected_verification_hash
            and actual_verification_hash == expected_verification_hash
        ),
        "expected_semantics_sha256": expected_semantics_hash or None,
        "actual_semantics_sha256": actual_semantics_hash,
        "expected_sse_verification_sha256": expected_verification_hash or None,
        "actual_sse_verification_sha256": actual_verification_hash,
    }


def validate_universe_source_semantics(raw_register_dir: Path) -> dict[str, Any]:
    """Verify source-wide date semantics from physical evidence, not local flags."""
    semantics = load_source_semantics(raw_register_dir)
    sources = semantics.get("sources") if isinstance(semantics, dict) else None
    if not isinstance(sources, dict):
        sources = {}

    required = ("szse_delisted_register.csv", "sse_delisted_register.csv", "bse_delisted_register.csv")
    missing_entries = [name for name in required if name not in sources]
    invalid_entries: list[str] = []
    unverified_evidence: list[str] = []
    source_results: dict[str, Any] = {}
    project_root = _project_root_for(raw_register_dir)

    for name in required:
        entry = sources.get(name)
        if not isinstance(entry, dict):
            continue
        raw_field = str(entry.get("raw_date_field") or "").strip()
        normalized = str(entry.get("normalized_field") or "").strip()
        status = str(entry.get("semantic_status") or "").strip().upper()
        allow = entry.get("allow_as_delisting_date") is True
        evidence = entry.get("source_wide_evidence") or entry.get("evidence") or {}
        if not isinstance(evidence, dict):
            evidence = {}
        evidence_audit = verify_source_artifact(project_root, evidence)
        valid_contract = bool(
            raw_field
            and normalized == "delisting_date"
            and allow
            and status == "SOURCE_WIDE_VERIFIED"
        )
        if not valid_contract:
            invalid_entries.append(name)
        if not evidence_audit["verified"]:
            unverified_evidence.append(name)
        source_results[name] = {
            "raw_date_field": raw_field,
            "normalized_field": normalized,
            "semantic_status": status,
            "allow_as_delisting_date": allow,
            "entry_valid": valid_contract,
            "reason": str(entry.get("reason") or ""),
            "source_wide_evidence": evidence_audit,
        }

    verification_path = raw_register_dir / SSE_VERIFICATION_FILE
    verification_rows = _read_csv(verification_path)
    verification_mismatches: list[str] = []
    verification_missing_fields: list[int] = []
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

    binding = _binding_audit(raw_register_dir)
    sample_content_valid = bool(
        verification_rows
        and not verification_mismatches
        and not verification_missing_fields
    )
    sample_hash_bound = bool(sample_content_valid and binding["verification_hash_match"])
    source_wide_verified = bool(
        not missing_entries
        and not invalid_entries
        and not unverified_evidence
        and binding["manifest_binding_present"]
        and binding["semantics_hash_match"]
        and sample_hash_bound
    )

    return {
        "manifest_present": bool(semantics),
        "manifest_path": str(raw_register_dir / SEMANTICS_FILE),
        "manifest_sha256": sha256_file(raw_register_dir / SEMANTICS_FILE) if (raw_register_dir / SEMANTICS_FILE).exists() else None,
        "manifest_binding_audit": binding,
        "required_sources": list(required),
        "missing_entries": missing_entries,
        "invalid_entries": sorted(set(invalid_entries)),
        "unverified_source_wide_evidence": sorted(set(unverified_evidence)),
        "sources": source_results,
        "sse_verification_file_present": verification_path.exists(),
        "sse_verification_file_sha256": sha256_file(verification_path) if verification_path.exists() else None,
        "sse_verification_sample_count": len(verification_rows),
        "sse_verification_mismatches": verification_mismatches,
        "sse_verification_missing_fields": verification_missing_fields,
        "sse_sample_evidence_valid": sample_content_valid,
        "sse_sample_evidence_hash_bound": sample_hash_bound,
        "source_wide_verified": source_wide_verified,
        "ready": source_wide_verified,
        "reason": "OK" if source_wide_verified else "DELISTING_DATE_SOURCE_SEMANTICS_NOT_INDEPENDENTLY_VERIFIED",
    }


def require_delisting_field_semantics(raw_register_dir: Path, source_file: str) -> dict[str, Any]:
    audit = validate_universe_source_semantics(raw_register_dir)
    if not audit.get("ready"):
        raise RuntimeError(f"unverified source-wide delisting-date semantics: {audit.get('reason')}")
    sources = load_source_semantics(raw_register_dir).get("sources") or {}
    entry = sources.get(source_file) if isinstance(sources, dict) else None
    if not isinstance(entry, dict):
        raise RuntimeError(f"missing source semantics entry for {source_file}")
    raw_field = str(entry.get("raw_date_field") or "").strip()
    if not raw_field:
        raise RuntimeError(f"missing raw_date_field semantics for {source_file}")
    return entry
