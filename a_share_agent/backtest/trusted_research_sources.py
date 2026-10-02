from __future__ import annotations

import csv
import json
from datetime import date as dt_date
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


def verify_source_artifact(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Verify one locally mounted source artifact against declared provenance.

    A syntactically valid hash is not evidence by itself. The source must have a
    document/URL identifier, a local artifact path, and the actual bytes must hash to
    the declared SHA256. Paths are restricted to the project root.
    """
    document = str(
        item.get("source_document_id_or_url")
        or item.get("source_url_or_document_id")
        or item.get("document_id")
        or item.get("url")
        or ""
    ).strip()
    declared = str(
        item.get("raw_source_hash")
        or item.get("sha256")
        or item.get("source_sha256")
        or ""
    ).strip().lower()
    raw_path = str(
        item.get("raw_source_path")
        or item.get("source_file")
        or item.get("path")
        or item.get("file")
        or ""
    ).strip()
    path = _resolve_inside(root, raw_path)
    exists = bool(path and path.is_file())
    actual = sha256_file(path) if exists and path is not None else None
    return {
        "source_document_id_or_url": document or None,
        "raw_source_path": raw_path or None,
        "declared_sha256": declared or None,
        "actual_sha256": actual,
        "artifact_exists": exists,
        "hash_match": bool(exists and _valid_sha256(declared) and actual == declared),
        "verified": bool(document and exists and _valid_sha256(declared) and actual == declared),
    }


def audit_official_trading_calendar(
    root: Path,
    *,
    research_start: str,
    research_end: str,
) -> dict[str, Any]:
    """Audit an independent official trading calendar.

    Required files:
      data/backtest/trusted_trading_calendar.csv
      data/backtest/trusted_trading_calendar_manifest.json

    The manifest must bind the calendar bytes and at least one source artifact. The
    calendar semantics must be explicit so a missing whole trading day cannot be
    silently treated as a market holiday.
    """
    calendar_path = root / "data" / "backtest" / "trusted_trading_calendar.csv"
    manifest_path = root / "data" / "backtest" / "trusted_trading_calendar_manifest.json"
    result: dict[str, Any] = {
        "verified": False,
        "reason": "TRUSTED_TRADING_CALENDAR_OR_MANIFEST_MISSING",
        "calendar_present": calendar_path.exists(),
        "manifest_present": manifest_path.exists(),
        "trading_dates": [],
        "trading_day_count": 0,
        "duplicate_dates": [],
        "malformed_rows": 0,
        "source_artifacts_verified": False,
    }
    if not calendar_path.exists() or not manifest_path.exists():
        return result

    manifest = _read_json(manifest_path)
    source_type = str(manifest.get("source_type") or "").strip()
    source_dataset_id = str(manifest.get("source_dataset_id") or "").strip()
    semantics = str(manifest.get("calendar_semantics") or "").strip().upper()
    expected_hash = str(manifest.get("calendar_sha256") or manifest.get("dataset_sha256") or "").strip().lower()
    actual_hash = sha256_file(calendar_path)
    rows = _read_csv(calendar_path)

    sources = manifest.get("source_files") or manifest.get("raw_source_files") or []
    if not isinstance(sources, list):
        sources = []
    source_audits = [verify_source_artifact(root, item) for item in sources if isinstance(item, dict)]
    sources_ok = bool(source_audits and len(source_audits) == len(sources) and all(x["verified"] for x in source_audits))

    all_dates: list[str] = []
    malformed = 0
    for row in rows:
        date = str(row.get("date") or row.get("trade_date") or "").strip()[:10]
        if not date:
            malformed += 1
            continue
        if semantics == "DATE_WITH_IS_OPEN":
            flag = str(row.get("is_open") or "").strip().lower()
            if flag in {"1", "true", "yes", "y", "open"}:
                all_dates.append(date)
            elif flag in {"0", "false", "no", "n", "closed"}:
                continue
            else:
                malformed += 1
        elif semantics == "OPEN_DATES_ONLY":
            all_dates.append(date)
        else:
            malformed += 1

    seen: set[str] = set()
    duplicates: list[str] = []
    for date in all_dates:
        if date in seen:
            duplicates.append(date)
        seen.add(date)
    window = sorted(date for date in seen if research_start <= date <= research_end)

    coverage_scope = str(manifest.get("coverage_scope") or "").strip().upper()
    coverage_start = str(manifest.get("coverage_start") or "").strip()
    coverage_end = str(manifest.get("coverage_end") or "").strip()

    scope_ok = (coverage_scope == "FULL_EXCHANGE_CALENDAR")
    range_contract_ok = bool(
        coverage_start
        and coverage_end
        and coverage_start <= research_start
        and coverage_end >= research_end
    )

    source_range_ok = True
    for item in sources:
        if isinstance(item, dict):
            src_start = str(item.get("coverage_start") or "").strip()
            src_end = str(item.get("coverage_end") or "").strip()
            if src_start and src_start > research_start:
                source_range_ok = False
            if src_end and src_end < research_end:
                source_range_ok = False

    min_date = min(all_dates) if all_dates else ""
    max_date = max(all_dates) if all_dates else ""

    content_span_ok = False
    if min_date and max_date:
        if semantics == "DATE_WITH_IS_OPEN":
            content_span_ok = bool(min_date <= research_start and max_date >= research_end)
        else:  # OPEN_DATES_ONLY
            try:
                d_min = dt_date.fromisoformat(min_date)
                d_r_start = dt_date.fromisoformat(research_start)
                d_max = dt_date.fromisoformat(max_date)
                d_r_end = dt_date.fromisoformat(research_end)
                start_ok = bool(d_min <= d_r_start or 0 <= (d_min - d_r_start).days <= 10)
                end_ok = bool(d_max >= d_r_end or 0 <= (d_r_end - d_max).days <= 10)
                content_span_ok = bool(start_ok and end_ok)
            except Exception:
                content_span_ok = False

    range_covered = bool(scope_ok and range_contract_ok and source_range_ok and content_span_ok)

    expected_count = manifest.get("row_count")
    try:
        expected_count_int = int(expected_count)
    except Exception:
        expected_count_int = -1
    count_match = expected_count_int == len(rows)

    verified = bool(
        source_type == "INDEPENDENT_OFFICIAL_EXPORT"
        and source_dataset_id
        and semantics in {"DATE_WITH_IS_OPEN", "OPEN_DATES_ONLY"}
        and _valid_sha256(expected_hash)
        and expected_hash == actual_hash
        and count_match
        and rows
        and not malformed
        and not duplicates
        and window
        and sources_ok
        and range_covered
    )
    result.update(
        {
            "verified": verified,
            "reason": "OK" if verified else (
                "TRUSTED_TRADING_CALENDAR_COVERAGE_RANGE_INCOMPLETE" if not range_covered
                else "TRUSTED_TRADING_CALENDAR_PROVENANCE_OR_CONTENT_INVALID"
            ),
            "source_type": source_type or None,
            "source_dataset_id": source_dataset_id or None,
            "calendar_semantics": semantics or None,
            "expected_calendar_sha256": expected_hash or None,
            "actual_calendar_sha256": actual_hash,
            "manifest_row_count": expected_count_int,
            "actual_row_count": len(rows),
            "row_count_match": count_match,
            "coverage_scope": coverage_scope or None,
            "coverage_start": coverage_start or None,
            "coverage_end": coverage_end or None,
            "calendar_data_start": min_date or None,
            "calendar_data_end": max_date or None,
            "coverage_scope_valid": scope_ok,
            "coverage_contract_valid": range_contract_ok,
            "source_artifacts_range_valid": source_range_ok,
            "calendar_data_range_valid": content_span_ok,
            "calendar_range_complete": range_covered,
            "trading_dates": window,
            "trading_day_count": len(window),
            "duplicate_dates": sorted(set(duplicates)),
            "malformed_rows": malformed,
            "source_artifacts_verified": sources_ok,
            "source_artifact_audits": source_audits,
        }
    )
    return result


def audit_trading_rule_provenance(root: Path, required_checks: list[str]) -> dict[str, Any]:
    """Bind every runtime trading-rule assertion to a physical official source artifact."""
    manifest_path = root / "data" / "backtest" / "trading_rules_provenance.json"
    manifest = _read_json(manifest_path)
    result: dict[str, Any] = {
        "verified": False,
        "manifest_present": manifest_path.exists(),
        "reason": "TRADING_RULE_PROVENANCE_MANIFEST_MISSING",
        "required_checks": sorted(set(required_checks)),
        "missing_checks": sorted(set(required_checks)),
        "invalid_checks": [],
    }
    if not manifest_path.exists() or not manifest:
        return result

    source_type = str(manifest.get("source_type") or "").strip()
    source_dataset_id = str(manifest.get("source_dataset_id") or "").strip()
    checks = manifest.get("rule_checks")
    if not isinstance(checks, dict):
        checks = {}

    missing: list[str] = []
    invalid: list[str] = []
    audits: dict[str, Any] = {}
    for name in sorted(set(required_checks)):
        item = checks.get(name)
        if not isinstance(item, dict):
            missing.append(name)
            continue
        reference = str(item.get("rule_reference") or item.get("article") or item.get("section") or "").strip()
        audit = verify_source_artifact(root, item)
        audit["rule_reference"] = reference or None
        audit["verified"] = bool(audit["verified"] and reference)
        audits[name] = audit
        if not audit["verified"]:
            invalid.append(name)

    verified = bool(
        source_type == "INDEPENDENT_OFFICIAL_RULE_DOCUMENTS"
        and source_dataset_id
        and required_checks
        and not missing
        and not invalid
    )
    result.update(
        {
            "verified": verified,
            "reason": "OK" if verified else "TRADING_RULE_PROVENANCE_INCOMPLETE_OR_INVALID",
            "source_type": source_type or None,
            "source_dataset_id": source_dataset_id or None,
            "missing_checks": missing,
            "invalid_checks": invalid,
            "check_audits": audits,
        }
    )
    return result
