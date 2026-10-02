from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file
from .provenance_audit import is_a_share_common_equity_symbol, local_record_active_on


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


def _board_for_symbol(symbol: str) -> str:
    code = symbol.split(".", 1)[0].zfill(6)
    if symbol.endswith(".BJ"):
        return "BSE"
    if symbol.endswith(".SH"):
        return "STAR" if code.startswith("688") else "SSE_MAIN"
    if symbol.endswith(".SZ"):
        return "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
    return "UNKNOWN"


def _normalize_symbol(code: str, suffix: str) -> str:
    value = str(code or "").strip().split(".", 1)[0].zfill(6)
    return f"{value}.{suffix}" if value else ""


def _date(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()[:10].replace("/", "-")
        if value:
            return value
    return ""


def _interval_bounds(row: dict[str, str]) -> tuple[str, str]:
    start = _date(row, "active_from", "listing_date")
    end = _date(row, "active_to", "delisting_date")
    return start or "0000-00-00", end or "9999-12-31"


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return max(a_start, b_start) <= min(a_end, b_end)


def _manifest_source_hash(snapshot_dir: Path, source_file: str) -> str:
    manifest = _read_json(snapshot_dir / "manifest.json")
    files = ((manifest.get("source_registers") or {}).get("files") or {}) if manifest else {}
    return str(files.get(source_file) or "").strip().lower() if isinstance(files, dict) else ""


def _source_semantics(snapshot_dir: Path, source_file: str) -> dict[str, Any]:
    semantics = _read_json(snapshot_dir / "raw_registers" / "source_semantics.json")
    sources = semantics.get("sources") if isinstance(semantics, dict) else None
    entry = sources.get(source_file) if isinstance(sources, dict) else None
    return entry if isinstance(entry, dict) else {}


def _parse_delisted_register(snapshot_dir: Path, source_file: str, suffix: str) -> dict[str, Any]:
    raw_dir = snapshot_dir / "raw_registers"
    path = raw_dir / source_file
    expected_hash = _manifest_source_hash(snapshot_dir, source_file)
    exists = path.exists()
    actual_hash = sha256_file(path) if exists else None
    hash_match = bool(exists and expected_hash and actual_hash == expected_hash)
    semantics = _source_semantics(snapshot_dir, source_file)
    raw_date_field = str(semantics.get("raw_date_field") or "").strip()
    normalized = str(semantics.get("normalized_field") or "").strip()
    allows = semantics.get("allow_as_delisting_date") is True
    semantic_ready = bool(raw_date_field and normalized == "delisting_date" and allows)

    rows: list[dict[str, str]] = []
    if exists:
        for raw in _read_csv(path):
            code = str(raw.get("证券代码") or raw.get("公司代码") or raw.get("股票代码") or raw.get("symbol") or "").strip()
            symbol = _normalize_symbol(code, suffix)
            board = _board_for_symbol(symbol)
            if not symbol or not is_a_share_common_equity_symbol(symbol, board):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "listing_date": _date(raw, "上市日期", "listing_date"),
                    "delisting_date": _date(raw, raw_date_field) if raw_date_field else "",
                }
            )
    return {
        "source_file": source_file,
        "exists": exists,
        "expected_sha256": expected_hash or None,
        "actual_sha256": actual_hash,
        "hash_match": hash_match,
        "semantic_ready": semantic_ready,
        "raw_date_field": raw_date_field or None,
        "semantic_status": str(semantics.get("semantic_status") or "") or None,
        "row_count": len(rows),
        "rows": rows,
    }


def audit_security_master_integrity(
    master_path: Path,
    snapshot_dir: Path,
    *,
    research_start: str,
    research_end: str,
    trading_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Validate PIT security-master boundaries and independent delisted-register coverage.

    Formal survivorship-bias protection must not rely on a boolean carried by the
    local UniverseInfo. It is proven here from interval invariants plus independent
    exchange delisted registers whose bytes and date semantics are verified.
    """
    master_rows = []
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _read_csv(master_path):
        symbol = str(row.get("symbol") or "").strip()
        board = str(row.get("board") or _board_for_symbol(symbol)).strip().upper()
        if not symbol or not is_a_share_common_equity_symbol(symbol, board):
            continue
        master_rows.append(row)
        by_symbol[symbol].append(row)

    missing_listing_date: list[str] = []
    active_before_listing: list[str] = []
    active_after_listing_start: list[str] = []
    active_after_delisting: list[str] = []
    active_before_delisting_end: list[str] = []
    listing_after_delisting: list[str] = []
    overlapping_membership: list[str] = []

    for symbol, rows in by_symbol.items():
        spans: list[tuple[str, str]] = []
        for row in rows:
            listing = _date(row, "listing_date")
            active_from = _date(row, "active_from")
            delisting = _date(row, "delisting_date")
            active_to = _date(row, "active_to")
            if not listing:
                missing_listing_date.append(symbol)
            if listing and active_from and active_from < listing:
                active_before_listing.append(symbol)
            if listing and active_from and active_from > listing:
                active_after_listing_start.append(symbol)
            if delisting and active_to and active_to > delisting:
                active_after_delisting.append(symbol)
            if delisting and active_to and active_to < delisting:
                active_before_delisting_end.append(symbol)
            if listing and delisting and listing > delisting:
                listing_after_delisting.append(symbol)
            spans.append(_interval_bounds(row))
        spans.sort()
        for i in range(1, len(spans)):
            if _overlaps(spans[i - 1][0], spans[i - 1][1], spans[i][0], spans[i][1]):
                overlapping_membership.append(symbol)
                break

    dates = sorted({str(d) for d in (trading_dates or []) if str(d)})
    daily_counts = []
    for d in dates:
        active = sum(
            1 for rows in by_symbol.values()
            if any(local_record_active_on(row, d) for row in rows)
        )
        daily_counts.append({"date": d, "active_symbols": active})

    source_specs = (
        ("szse_delisted_register.csv", "SZ"),
        ("sse_delisted_register.csv", "SH"),
        ("bse_delisted_register.csv", "BJ"),
    )
    source_audits: dict[str, Any] = {}
    expected_delisted: dict[str, dict[str, str]] = {}
    unresolved_sources: list[str] = []
    for source_file, suffix in source_specs:
        audit = _parse_delisted_register(snapshot_dir, source_file, suffix)
        source_audits[source_file] = {k: v for k, v in audit.items() if k != "rows"}
        if not audit["exists"] or not audit["hash_match"] or not audit["semantic_ready"]:
            unresolved_sources.append(source_file)
            continue
        for row in audit["rows"]:
            listing = row["listing_date"]
            delisting = row["delisting_date"]
            if not listing or not delisting:
                continue
            if listing <= research_end and delisting >= research_start:
                expected_delisted[row["symbol"]] = row

    missing_delisted_symbols: list[str] = []
    delisted_listing_date_mismatch: list[str] = []
    delisted_date_mismatch: list[str] = []
    for symbol, expected in expected_delisted.items():
        local_rows = by_symbol.get(symbol, [])
        if not local_rows:
            missing_delisted_symbols.append(symbol)
            continue
        if not any(_date(row, "listing_date") == expected["listing_date"] for row in local_rows):
            delisted_listing_date_mismatch.append(symbol)
        if not any(_date(row, "delisting_date", "active_to") == expected["delisting_date"] for row in local_rows):
            delisted_date_mismatch.append(symbol)

    boundary_integrity = not any(
        (
            missing_listing_date,
            active_before_listing,
            active_after_listing_start,
            active_after_delisting,
            active_before_delisting_end,
            listing_after_delisting,
            overlapping_membership,
        )
    )
    delisted_register_complete = bool(
        not unresolved_sources
        and expected_delisted
        and not missing_delisted_symbols
        and not delisted_listing_date_mismatch
        and not delisted_date_mismatch
    )
    return {
        "target_master_row_count": len(master_rows),
        "target_master_symbol_count": len(by_symbol),
        "boundary_integrity": boundary_integrity,
        "missing_listing_date_symbols": sorted(set(missing_listing_date)),
        "prelisting_leakage_symbols": sorted(set(active_before_listing)),
        "late_active_start_symbols": sorted(set(active_after_listing_start)),
        "post_delisting_leakage_symbols": sorted(set(active_after_delisting)),
        "early_active_end_symbols": sorted(set(active_before_delisting_end)),
        "listing_after_delisting_symbols": sorted(set(listing_after_delisting)),
        "overlapping_membership_symbols": sorted(set(overlapping_membership)),
        "trading_date_count": len(dates),
        "min_daily_active_symbols": min((x["active_symbols"] for x in daily_counts), default=0),
        "max_daily_active_symbols": max((x["active_symbols"] for x in daily_counts), default=0),
        "daily_active_counts": daily_counts,
        "delisted_source_audits": source_audits,
        "unresolved_delisted_sources": unresolved_sources,
        "expected_delisted_symbols_in_period": len(expected_delisted),
        "missing_delisted_symbols": sorted(missing_delisted_symbols),
        "delisted_listing_date_mismatch_symbols": sorted(delisted_listing_date_mismatch),
        "delisted_date_mismatch_symbols": sorted(delisted_date_mismatch),
        "delisted_register_complete": delisted_register_complete,
        "survivorship_bias_protection_ready": bool(boundary_integrity and delisted_register_complete),
    }
