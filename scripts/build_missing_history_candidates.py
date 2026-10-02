#!/usr/bin/env python3
"""Build an auditable remediation matrix for missing historical Universe symbols.

The script does not mutate ``security_master.csv`` and does not download market data.
It joins the current unique Universe gaps to independent exchange raw registers and the
source-semantics contract so downstream work can distinguish a source-proven date from
an ambiguous one.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from a_share_agent.backtest.universe_source_semantics import load_source_semantics

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "data" / "backtest"
RAW_DIR = BACKTEST / "official_universe_snapshots" / "raw_registers"
AUDIT_JSON = ROOT / "historical_data_provenance_audit.json"
OUT_CSV = ROOT / "security_master_missing_history_candidates.csv"
OUT_JSON = ROOT / "security_master_missing_history_candidates_summary.json"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _load_missing_symbols() -> list[str]:
    if not AUDIT_JSON.exists():
        raise RuntimeError(f"missing audit artifact: {AUDIT_JSON}")
    data = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    universe = data.get("universe") or {}
    symbols = universe.get("unique_missing_symbols") or []
    if not isinstance(symbols, list) or not symbols:
        raise RuntimeError("audit artifact has no unique_missing_symbols")
    return sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})


def _raw_maps() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    szse: dict[str, dict[str, str]] = {}
    for row in _read_csv(RAW_DIR / "szse_delisted_register.csv"):
        code = str(row.get("证券代码") or "").strip().zfill(6)
        if code:
            szse[f"{code}.SZ"] = row

    sse: dict[str, dict[str, str]] = {}
    for row in _read_csv(RAW_DIR / "sse_delisted_register.csv"):
        code = str(row.get("公司代码") or "").strip().zfill(6)
        if code and not code.startswith("900"):
            sse[f"{code}.SH"] = row
    return szse, sse


def _board(symbol: str) -> str:
    code = symbol.split(".", 1)[0]
    if symbol.endswith(".SZ"):
        return "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
    if symbol.endswith(".SH"):
        return "STAR" if code.startswith("688") else "SSE_MAIN"
    if symbol.endswith(".BJ"):
        return "BSE"
    return "UNKNOWN"


def build_candidates() -> dict[str, Any]:
    missing = _load_missing_symbols()
    szse_map, sse_map = _raw_maps()
    semantics = load_source_semantics(RAW_DIR)
    source_semantics = semantics.get("sources") if isinstance(semantics, dict) else {}
    if not isinstance(source_semantics, dict):
        source_semantics = {}

    rows: list[dict[str, str]] = []
    unresolved_source = 0
    date_semantics_ready = 0
    blocked_semantics = 0

    for symbol in missing:
        if symbol.endswith(".SZ"):
            source_file = "szse_delisted_register.csv"
            raw = szse_map.get(symbol)
            name_field = "证券简称"
            listing_field = "上市日期"
        elif symbol.endswith(".SH"):
            source_file = "sse_delisted_register.csv"
            raw = sse_map.get(symbol)
            name_field = "公司简称"
            listing_field = "上市日期"
        else:
            source_file = ""
            raw = None
            name_field = ""
            listing_field = ""

        entry = source_semantics.get(source_file) if source_file else None
        if not isinstance(entry, dict):
            entry = {}
        raw_date_field = str(entry.get("raw_date_field") or "").strip()
        semantic_status = str(entry.get("semantic_status") or "MISSING_SOURCE_SEMANTICS").strip()
        allow_date = entry.get("allow_as_delisting_date") is True
        raw_found = raw is not None
        raw_date = str((raw or {}).get(raw_date_field) or "").strip() if raw_date_field else ""
        listing_date = str((raw or {}).get(listing_field) or "").strip()
        name = str((raw or {}).get(name_field) or "").strip()

        if not raw_found:
            candidate_status = "BLOCKED_OFFICIAL_RAW_RECORD_NOT_FOUND"
            recommended = "verify official register coverage before changing security_master"
            unresolved_source += 1
        elif not allow_date:
            candidate_status = "BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS"
            recommended = "verify official delisting/摘牌 date semantics, then collect Raw/Status/Sector before master insertion"
            blocked_semantics += 1
        elif not listing_date or not raw_date:
            candidate_status = "BLOCKED_MISSING_REQUIRED_DATE_FIELD"
            recommended = "resolve missing official listing/delisting dates before master insertion"
            unresolved_source += 1
        else:
            candidate_status = "DATE_PROVENANCE_READY_MASTER_INSERT_NOT_YET_APPROVED"
            recommended = "collect/verify Raw OHLCV, historical Status and Sector provenance; then build a PIT master record without synthetic defaults"
            date_semantics_ready += 1

        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "exchange": "SZSE" if symbol.endswith(".SZ") else ("SSE" if symbol.endswith(".SH") else "UNKNOWN"),
                "board": _board(symbol),
                "listing_date": listing_date,
                "raw_delisting_date": raw_date,
                "raw_delisting_field": raw_date_field,
                "source_file": source_file,
                "raw_record_found": str(raw_found),
                "source_semantic_status": semantic_status,
                "source_allows_delisting_date": str(allow_date),
                "candidate_status": candidate_status,
                "data_missing": "1",
                "master_insert_approved": "False",
                "recommended_action": recommended,
            }
        )

    fields = [
        "symbol",
        "name",
        "exchange",
        "board",
        "listing_date",
        "raw_delisting_date",
        "raw_delisting_field",
        "source_file",
        "raw_record_found",
        "source_semantic_status",
        "source_allows_delisting_date",
        "candidate_status",
        "data_missing",
        "master_insert_approved",
        "recommended_action",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "candidate_count": len(rows),
        "date_provenance_ready_count": date_semantics_ready,
        "blocked_source_semantics_count": blocked_semantics,
        "unresolved_source_record_count": unresolved_source,
        "by_exchange": {
            "SZSE": sum(1 for row in rows if row["exchange"] == "SZSE"),
            "SSE": sum(1 for row in rows if row["exchange"] == "SSE"),
            "BSE": sum(1 for row in rows if row["exchange"] == "BSE"),
        },
        "master_insert_approved_count": 0,
        "output_csv": OUT_CSV.name,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    print(json.dumps(build_candidates(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
