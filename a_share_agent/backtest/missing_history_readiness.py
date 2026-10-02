from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from .data_integrity import (
    sha256_file,
    verify_coverage_binding,
    verify_raw_dataset_manifest,
)
from ..git_utils import get_git_metadata


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _valid_sha256(value: str) -> bool:
    value = str(value or "").strip().lower()
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _iso(value: Any) -> str:
    raw = str(value or "").strip()[:10].replace("/", "-")
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def _resolve_artifact_path(root: Path, sidecar_path: Path, row: dict[str, str]) -> Path | None:
    raw = str(
        row.get("raw_source_path")
        or row.get("source_file_path")
        or row.get("source_path")
        or row.get("raw_source_file")
        or ""
    ).strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    root_candidate = root / p
    if root_candidate.exists():
        return root_candidate
    return sidecar_path.parent / p


def _verified_source_ids(root: Path, sidecar_path: Path) -> tuple[set[str], dict[str, Any]]:
    valid: set[str] = set()
    invalid_rows = 0
    missing_artifacts = 0
    hash_mismatches = 0
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
        artifact = _resolve_artifact_path(root, sidecar_path, row)
        if not source_id or not document or not _valid_sha256(digest) or artifact is None:
            invalid_rows += 1
            continue
        if not artifact.exists():
            missing_artifacts += 1
            continue
        if sha256_file(artifact) != digest:
            hash_mismatches += 1
            continue
        valid.add(source_id)
    return valid, {
        "sidecar_present": sidecar_path.exists(),
        "valid_source_ids": len(valid),
        "invalid_sidecar_rows": invalid_rows,
        "missing_source_artifacts": missing_artifacts,
        "source_artifact_hash_mismatches": hash_mismatches,
    }


def _candidate_source_membership_status(root: Path, candidate: dict[str, str]) -> dict[str, Any]:
    symbol = str(candidate.get("symbol") or "").strip()
    source_file = str(candidate.get("source_file") or "").strip()
    listing_date = _iso(candidate.get("listing_date"))
    delisting_date = _iso(candidate.get("raw_delisting_date"))
    snapshot_dir = root / "data" / "backtest" / "official_universe_snapshots"
    raw_dir = snapshot_dir / "raw_registers"
    register_path = raw_dir / source_file if source_file else Path()
    manifest = _load_json(snapshot_dir / "manifest.json")
    source_hashes = ((manifest.get("source_registers") or {}).get("files") or {}) if manifest else {}
    expected_hash = str(source_hashes.get(source_file) or "").strip().lower() if isinstance(source_hashes, dict) else ""
    register_exists = bool(source_file and register_path.exists())
    actual_hash = sha256_file(register_path) if register_exists else None
    register_hash_match = bool(register_exists and _valid_sha256(expected_hash) and actual_hash == expected_hash)

    semantics = _load_json(raw_dir / "source_semantics.json")
    sources = semantics.get("sources") if isinstance(semantics, dict) else {}
    entry = sources.get(source_file) if isinstance(sources, dict) and source_file else None
    if not isinstance(entry, dict):
        entry = {}
    raw_date_field = str(entry.get("raw_date_field") or "").strip()
    normalized_field = str(entry.get("normalized_field") or "").strip()
    semantic_status = str(entry.get("semantic_status") or "").strip()
    source_allows = entry.get("allow_as_delisting_date") is True

    code = symbol.split(".", 1)[0]
    matched: dict[str, str] | None = None
    if register_exists:
        for row in _read_csv(register_path):
            row_code = str(
                row.get("证券代码")
                or row.get("公司代码")
                or row.get("股票代码")
                or row.get("symbol")
                or ""
            ).strip().split(".", 1)[0].zfill(6)
            if row_code == code.zfill(6):
                matched = row
                break

    source_listing_date = _iso((matched or {}).get("上市日期") or (matched or {}).get("listing_date"))
    source_delisting_date = _iso((matched or {}).get(raw_date_field)) if raw_date_field else ""
    dates_match = bool(
        matched
        and listing_date
        and delisting_date
        and source_listing_date == listing_date
        and source_delisting_date == delisting_date
    )
    verified = bool(
        symbol
        and register_hash_match
        and matched
        and source_allows
        and normalized_field == "delisting_date"
        and raw_date_field
        and dates_match
    )
    return {
        "membership_source_verified": verified,
        "membership_source_file": source_file or None,
        "membership_source_register_exists": register_exists,
        "membership_source_register_hash_match": register_hash_match,
        "membership_source_expected_hash": expected_hash or None,
        "membership_source_actual_hash": actual_hash,
        "membership_source_record_found": matched is not None,
        "membership_source_semantic_status": semantic_status or None,
        "membership_source_allows_delisting_date": source_allows,
        "membership_source_listing_date": source_listing_date or None,
        "membership_source_delisting_date": source_delisting_date or None,
        "membership_source_dates_match_candidate": dates_match,
    }


def _dataset_context(root: Path) -> dict[str, Any]:
    manifest_path = root / "raw_dataset_manifest.json"
    manifest = _load_json(manifest_path)
    integrity = verify_raw_dataset_manifest(root / "data" / "backtest" / "raw_prices", manifest_path)
    coverage_binding = verify_coverage_binding(
        root / "daily_raw_coverage.csv",
        root / "daily_raw_coverage_manifest.json",
        integrity.get("actual_raw_dataset_hash"),
    )
    dr = manifest.get("date_range") if isinstance(manifest.get("date_range"), dict) else {}
    start = _iso((dr or {}).get("start"))
    end = _iso((dr or {}).get("end"))
    calendar: list[str] = []
    if coverage_binding.get("daily_raw_coverage_fresh"):
        for row in _read_csv(root / "daily_raw_coverage.csv"):
            d = _iso(row.get("date") or row.get("trade_date"))
            if d:
                calendar.append(d)
    calendar = sorted(set(calendar))
    return {
        "manifest": manifest,
        "integrity": integrity,
        "coverage_binding": coverage_binding,
        "dataset_start": start,
        "dataset_end": end,
        "calendar": calendar,
    }


def _active_window(candidate: dict[str, str], context: dict[str, Any]) -> tuple[str, str, list[str]]:
    listing = _iso(candidate.get("listing_date"))
    delisting = _iso(candidate.get("raw_delisting_date"))
    dataset_start = str(context.get("dataset_start") or "")
    dataset_end = str(context.get("dataset_end") or "")
    if not listing or not delisting or not dataset_start or not dataset_end:
        return "", "", []
    start = max(listing, dataset_start)
    end = min(delisting, dataset_end)
    if end < start:
        return start, end, []
    dates = [d for d in context.get("calendar", []) if start <= d <= end]
    return start, end, dates


def _interval_window_status(
    root: Path,
    interval_path: Path,
    sidecar_path: Path,
    symbol: str,
    expected_dates: list[str],
    value_fields: tuple[str, ...],
) -> dict[str, Any]:
    rows = [row for row in _read_csv(interval_path) if str(row.get("symbol") or "").strip() == symbol]
    valid_sources, sidecar_audit = _verified_source_ids(root, sidecar_path)
    source_provenance_ok = bool(rows) and bool(valid_sources) and all(
        str(row.get("source") or "").strip() in valid_sources for row in rows
    )
    gaps: list[str] = []
    conflicts: list[str] = []
    values_by_date: dict[str, str] = {}
    for d in expected_dates:
        matching = []
        for row in rows:
            start = _iso(row.get("effective_from") or row.get("start_date"))
            end = _iso(row.get("effective_to") or row.get("end_date"))
            if start and d < start:
                continue
            if end and d > end:
                continue
            matching.append(row)
        if not matching:
            gaps.append(d)
            continue
        values = {
            str(next((row.get(field) for field in value_fields if row.get(field) not in (None, "")), "")).strip().upper()
            for row in matching
        }
        values.discard("")
        if len(values) > 1:
            conflicts.append(d)
        values_by_date[d] = sorted(values)[0] if values else ""
    coverage_complete = bool(expected_dates) and not gaps and not conflicts
    return {
        "interval_count": len(rows),
        "source_provenance_verified": source_provenance_ok,
        "window_coverage_complete": coverage_complete,
        "gap_day_count": len(gaps),
        "conflict_day_count": len(conflicts),
        "gap_days_sample": gaps[:10],
        "conflict_days_sample": conflicts[:10],
        "values_by_date": values_by_date,
        "sidecar_audit": sidecar_audit,
    }


def _raw_file_status(
    root: Path,
    symbol: str,
    expected_dates: list[str],
    context: dict[str, Any],
    status_values_by_date: dict[str, str],
) -> dict[str, Any]:
    filename = symbol.replace(".", "_") + ".csv"
    raw_path = root / "data" / "backtest" / "raw_prices" / filename
    manifest = context.get("manifest") if isinstance(context.get("manifest"), dict) else {}
    file_hashes = manifest.get("file_hashes") if isinstance(manifest, dict) else None
    if not isinstance(file_hashes, dict):
        file_hashes = {}
    expected_hash = str(file_hashes.get(filename) or "").strip().lower()
    exists = raw_path.exists()
    actual_hash = sha256_file(raw_path) if exists else None
    file_hash_match = bool(exists and _valid_sha256(expected_hash) and actual_hash == expected_hash)

    raw_dates: set[str] = set()
    if exists:
        for row in _read_csv(raw_path):
            d = _iso(row.get("date") or row.get("trade_date"))
            if d:
                raw_dates.add(d)
    expected_raw_dates = [
        d for d in expected_dates
        if str(status_values_by_date.get(d) or "").upper() not in {"SUSPENDED", "DELISTED"}
    ]
    missing_dates = [d for d in expected_raw_dates if d not in raw_dates]
    present = len(expected_raw_dates) - len(missing_dates)
    window_coverage = present / len(expected_raw_dates) if expected_raw_dates else 0.0
    dataset_hash_match = bool((context.get("integrity") or {}).get("raw_dataset_hash_match"))
    coverage_fresh = bool((context.get("coverage_binding") or {}).get("daily_raw_coverage_fresh"))
    window_complete = bool(expected_raw_dates and not missing_dates)
    return {
        "raw_filename": filename,
        "raw_file_exists": exists,
        "raw_manifest_hash_present": _valid_sha256(expected_hash),
        "raw_file_hash_match": file_hash_match,
        "expected_raw_file_hash": expected_hash or None,
        "actual_raw_file_hash": actual_hash,
        "raw_dataset_hash_match": dataset_hash_match,
        "daily_raw_coverage_fresh": coverage_fresh,
        "raw_expected_trading_days": len(expected_raw_dates),
        "raw_present_trading_days": present,
        "raw_missing_trading_days": len(missing_dates),
        "raw_missing_dates_sample": missing_dates[:10],
        "raw_window_coverage": round(window_coverage, 6),
        "raw_window_complete": window_complete,
    }


def assess_missing_history_candidate(
    root: Path,
    candidate: dict[str, str],
    *,
    dataset_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess PIT membership readiness separately from full execution readiness.

    Candidate CSV fields are treated only as a work queue. Membership readiness is
    re-derived from the current hash-bound official raw register and source-semantics
    contract. Execution readiness additionally requires a hash-verified Raw dataset,
    a fresh trading-calendar/coverage binding, complete Raw bars for every non-suspended
    trading date in the candidate's active research window, and gap-free Status/Sector
    intervals whose sidecars bind to real source artifacts on disk.
    """
    symbol = str(candidate.get("symbol") or "").strip()
    listing_date = _iso(candidate.get("listing_date"))
    delisting_date = _iso(candidate.get("raw_delisting_date"))
    membership_source = _candidate_source_membership_status(root, candidate) if symbol else {
        "membership_source_verified": False,
        "membership_source_register_hash_match": False,
        "membership_source_record_found": False,
        "membership_source_allows_delisting_date": False,
        "membership_source_dates_match_candidate": False,
    }
    membership_ready = bool(membership_source.get("membership_source_verified"))

    context = dataset_context or _dataset_context(root)
    window_start, window_end, expected_dates = _active_window(candidate, context)
    status = _interval_window_status(
        root,
        root / "data" / "backtest" / "historical_status_intervals.csv",
        root / "data" / "backtest" / "status_provenance.csv",
        symbol,
        expected_dates,
        ("status",),
    )
    sector = _interval_window_status(
        root,
        root / "data" / "backtest" / "historical_sector_intervals.csv",
        root / "data" / "backtest" / "sector_provenance.csv",
        symbol,
        expected_dates,
        ("sector_code", "industry_code", "sector", "industry"),
    )
    raw = _raw_file_status(root, symbol, expected_dates, context, status["values_by_date"]) if symbol else {}

    status_ready = bool(status["source_provenance_verified"] and status["window_coverage_complete"])
    sector_ready = bool(sector["source_provenance_verified"] and sector["window_coverage_complete"])
    raw_ready = bool(
        raw.get("raw_dataset_hash_match")
        and raw.get("daily_raw_coverage_fresh")
        and raw.get("raw_file_hash_match")
        and raw.get("raw_window_complete")
    )
    execution_ready = bool(membership_ready and expected_dates and raw_ready and status_ready and sector_ready)

    blockers: list[str] = []
    if not membership_ready:
        blockers.append("PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE")
    if not raw.get("raw_file_hash_match"):
        blockers.append("RAW_OHLCV_NOT_MANIFEST_VERIFIED")
    elif not raw.get("raw_dataset_hash_match") or not raw.get("daily_raw_coverage_fresh"):
        blockers.append("RAW_DATASET_OR_COVERAGE_BINDING_INVALID")
    elif not raw.get("raw_window_complete"):
        blockers.append("RAW_OHLCV_WINDOW_INCOMPLETE")
    if not status["source_provenance_verified"]:
        blockers.append("HISTORICAL_STATUS_PROVENANCE_INCOMPLETE")
    elif not status["window_coverage_complete"]:
        blockers.append("HISTORICAL_STATUS_WINDOW_INCOMPLETE")
    if not sector["source_provenance_verified"]:
        blockers.append("HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE")
    elif not sector["window_coverage_complete"]:
        blockers.append("HISTORICAL_SECTOR_WINDOW_INCOMPLETE")
    if not expected_dates:
        blockers.append("RESEARCH_WINDOW_TRADING_CALENDAR_UNAVAILABLE")

    return {
        "symbol": symbol,
        "membership_ready": membership_ready,
        "execution_ready": execution_ready,
        "listing_date": listing_date,
        "delisting_date": delisting_date,
        "candidate_status": str(candidate.get("candidate_status") or "").strip(),
        "research_window_start": window_start or None,
        "research_window_end": window_end or None,
        "research_window_trading_days": len(expected_dates),
        "status_interval_count": status["interval_count"],
        "status_provenance_verified": status["source_provenance_verified"],
        "status_window_coverage_complete": status["window_coverage_complete"],
        "status_gap_day_count": status["gap_day_count"],
        "status_conflict_day_count": status["conflict_day_count"],
        "sector_interval_count": sector["interval_count"],
        "sector_provenance_verified": sector["source_provenance_verified"],
        "sector_window_coverage_complete": sector["window_coverage_complete"],
        "sector_gap_day_count": sector["gap_day_count"],
        "sector_conflict_day_count": sector["conflict_day_count"],
        "blockers": blockers,
        **membership_source,
        **raw,
    }


def audit_missing_history_candidates(root: Path, candidates_path: Path | None = None) -> dict[str, Any]:
    candidates_path = candidates_path or (root / "security_master_missing_history_candidates.csv")
    candidates = _read_csv(candidates_path)
    context = _dataset_context(root)
    rows = [assess_missing_history_candidate(root, row, dataset_context=context) for row in candidates]
    blocker_counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("blockers") or []:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    git_meta = get_git_metadata(root)
    return {
        "candidate_count": len(rows),
        "membership_ready_count": sum(1 for row in rows if row["membership_ready"]),
        "execution_ready_count": sum(1 for row in rows if row["execution_ready"]),
        "blocked_membership_count": sum(1 for row in rows if not row["membership_ready"]),
        "blocked_execution_count": sum(1 for row in rows if not row["execution_ready"]),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "raw_dataset_hash_match": bool((context.get("integrity") or {}).get("raw_dataset_hash_match")),
        "daily_raw_coverage_fresh": bool((context.get("coverage_binding") or {}).get("daily_raw_coverage_fresh")),
        "producer_git_commit": git_meta.get("git_commit_sha"),
        "producer_code_version": git_meta.get("producer_code_version"),
        "generated_at": git_meta.get("generated_at"),
        "rows": rows,
    }
