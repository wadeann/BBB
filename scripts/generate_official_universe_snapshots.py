#!/usr/bin/env python3
"""Generate independent point-in-time A-share common-equity universe snapshots.

Raw registers are exchange exports and are never derived from ``security_master.csv``.
Non-target securities (B shares, CDRs, funds, convertibles, preferred shares, etc.) are
excluded before reconciliation.  Delisting boundaries are accepted only when a source
semantics contract explicitly allows the raw date field to be normalized as
``delisting_date``.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from a_share_agent.backtest.universe_source_semantics import (
    require_delisting_field_semantics,
    validate_universe_source_semantics,
)

ROOT = Path(__file__).resolve().parent.parent
RAW_REG_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
SNAPSHOT_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots"
ASSET_AUDIT_FILE = ROOT / "official_universe_asset_type_audit.csv"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_security(code: str, market: str) -> tuple[str, bool, str]:
    code = code.strip().zfill(6)
    if market == "SSE_MAIN":
        if code.startswith(("600", "601", "603", "605")):
            return "A_SHARE_COMMON_EQUITY", True, ""
        if code.startswith("900"):
            return "B_SHARE", False, "B shares are outside the A-share common-equity research universe"
    elif market == "STAR":
        if code.startswith("688"):
            return "A_SHARE_COMMON_EQUITY", True, ""
        if code.startswith("689"):
            return "CDR", False, "CDRs are excluded from A-share common equity"
    elif market == "SZSE":
        if code.startswith(("000", "001", "002", "003", "300", "301")):
            return "A_SHARE_COMMON_EQUITY", True, ""
        if code.startswith(("200", "201")):
            return "B_SHARE", False, "B shares are outside the A-share common-equity research universe"
    elif market == "BSE":
        if code.startswith(("43", "83", "87", "88", "92")):
            return "A_SHARE_COMMON_EQUITY", True, ""
    return "OTHER_SECURITY", False, "Security code/share class is outside the configured A_SHARE_COMMON_EQUITY universe"


def load_raw_official_registers() -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    official: dict[str, dict[str, str]] = {}
    audit: list[dict[str, str]] = []

    def add_row(
        *,
        raw_file: str,
        code: str,
        name: str,
        market: str,
        board: str,
        listing_date: str,
        delisting_date: str = "",
        source: str,
        url: str,
    ) -> None:
        asset_type, include, reason = classify_security(code, market)
        suffix = ".BJ" if market == "BSE" else (".SZ" if market == "SZSE" else ".SH")
        sym = f"{code.strip().zfill(6)}{suffix}"
        audit.append(
            {
                "raw_source_file": raw_file,
                "symbol": sym,
                "security_type": asset_type,
                "included": str(include),
                "exclusion_reason": reason,
            }
        )
        if not include:
            return
        rec = {
            "symbol": sym,
            "name": name.strip(),
            "exchange": "BSE" if market == "BSE" else ("SZSE" if market == "SZSE" else "SSE"),
            "board": board,
            "security_type": asset_type,
            "listing_date": listing_date.strip(),
            "delisting_date": delisting_date.strip(),
            "source": source,
            "source_document_id_or_url": url,
            "dataset_version": "2026.09.30",
        }
        if sym in official:
            if rec["delisting_date"]:
                official[sym]["delisting_date"] = rec["delisting_date"]
            if rec["listing_date"] and not official[sym].get("listing_date"):
                official[sym]["listing_date"] = rec["listing_date"]
        else:
            official[sym] = rec

    path = RAW_REG_DIR / "sse_main_listing_register.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["证券代码"].strip()
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["证券简称"],
                    market="SSE_MAIN",
                    board="SSE_MAIN",
                    listing_date=row["上市日期"],
                    source="SSE_OFFICIAL_LISTING_REGISTER",
                    url=f"https://www.sse.com.cn/assortment/stock/list/info/price/index.shtml?COMPANY_CODE={code}",
                )

    path = RAW_REG_DIR / "star_listing_register.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["证券代码"].strip()
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["证券简称"],
                    market="STAR",
                    board="STAR",
                    listing_date=row["上市日期"],
                    source="SSE_OFFICIAL_LISTING_REGISTER",
                    url=f"https://star.sse.com.cn/company/detail.shtml?stockCode={code}",
                )

    path = RAW_REG_DIR / "szse_listing_register.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["A股代码"].strip().zfill(6)
                board = "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["A股简称"],
                    market="SZSE",
                    board=board,
                    listing_date=row["A股上市日期"],
                    source="SZSE_OFFICIAL_LISTING_REGISTER",
                    url=f"https://www.szse.cn/market/product/stock/list/index.html?stockCode={code}",
                )

    path = RAW_REG_DIR / "bse_listing_register.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["证券代码"].strip()
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["证券简称"],
                    market="BSE",
                    board="BSE",
                    listing_date=row["上市日期"],
                    source="BSE_OFFICIAL_LISTING_REGISTER",
                    url=f"https://www.bse.cn/company/company_detail.html?stockCode={code}",
                )

    path = RAW_REG_DIR / "sse_delisted_register.csv"
    if path.exists():
        semantics = require_delisting_field_semantics(RAW_REG_DIR, path.name)
        raw_date_field = str(semantics["raw_date_field"])
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["公司代码"].strip()
                board = "STAR" if code.startswith(("688", "689")) else "SSE_MAIN"
                market = "STAR" if board == "STAR" else "SSE_MAIN"
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["公司简称"],
                    market=market,
                    board=board,
                    listing_date=row["上市日期"],
                    delisting_date=str(row.get(raw_date_field) or "").strip(),
                    source="SSE_OFFICIAL_DELISTED_REGISTER",
                    url=f"https://www.sse.com.cn/assortment/stock/list/delist/info/price/index.shtml?COMPANY_CODE={code}",
                )

    path = RAW_REG_DIR / "szse_delisted_register.csv"
    if path.exists():
        semantics = require_delisting_field_semantics(RAW_REG_DIR, path.name)
        raw_date_field = str(semantics["raw_date_field"])
        with path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["证券代码"].strip().zfill(6)
                board = "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
                add_row(
                    raw_file=path.name,
                    code=code,
                    name=row["证券简称"],
                    market="SZSE",
                    board=board,
                    listing_date=row["上市日期"],
                    delisting_date=str(row.get(raw_date_field) or "").strip(),
                    source="SZSE_OFFICIAL_DELISTED_REGISTER",
                    url=f"https://www.szse.cn/market/stock/suspend/index.html?stockCode={code}",
                )

    with ASSET_AUDIT_FILE.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["raw_source_file", "symbol", "security_type", "included", "exclusion_reason"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(audit)
    return official, audit


def generate_snapshots() -> dict:
    semantics_audit = validate_universe_source_semantics(RAW_REG_DIR)
    if not semantics_audit["source_wide_verified"]:
        raise RuntimeError(
            "OFFICIAL_UNIVERSE_SOURCE_SEMANTICS_BLOCKED: "
            + str(semantics_audit.get("reason") or "unverified date-field semantics")
        )

    official, audit = load_raw_official_registers()
    dates = ["2024-10-08", "2025-09-29", "2026-07-01", "2026-08-31", "2026-09-30"]
    fields = [
        "date",
        "symbol",
        "exchange",
        "board",
        "security_type",
        "listing_date",
        "source",
        "source_document_id_or_url",
        "dataset_version",
    ]
    stats: dict[str, dict] = {}
    for date in dates:
        rows = []
        for _, rec in sorted(official.items()):
            if rec["listing_date"] and rec["listing_date"] <= date and (
                not rec["delisting_date"] or rec["delisting_date"] >= date
            ):
                rows.append({key: rec[key] if key != "date" else date for key in fields})
        out = SNAPSHOT_DIR / f"{date}.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        boards = defaultdict(int)
        for row in rows:
            boards[row["board"]] += 1
        stats[date] = {
            "file": str(out.relative_to(ROOT)),
            "total_symbols": len(rows),
            "by_board": dict(boards),
            "sha256": sha256_file(out),
            "source": "INDEPENDENT_EXCHANGE_REGISTERS_FILTERED_TO_A_SHARE_COMMON_EQUITY",
        }

    excluded = [row for row in audit if row["included"] == "False"]
    raw_hashes = {path.name: sha256_file(path) for path in sorted(RAW_REG_DIR.glob("*.csv"))}
    manifest = {
        "manifest_id": "official_universe_snapshots_v0.7.7",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_asset_type": "A_SHARE_COMMON_EQUITY",
        "source_semantics": semantics_audit,
        "source_registers": {
            "directory": str(RAW_REG_DIR.relative_to(ROOT)) + "/",
            "files": raw_hashes,
        },
        "asset_type_audit": {
            "file": ASSET_AUDIT_FILE.name,
            "included_rows": len(audit) - len(excluded),
            "excluded_rows": len(excluded),
            "excluded_by_type": {
                asset_type: sum(1 for row in excluded if row["security_type"] == asset_type)
                for asset_type in sorted({row["security_type"] for row in excluded})
            },
        },
        "snapshots": stats,
    }
    (ROOT / "official_universe_snapshot_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (SNAPSHOT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Generated {len(stats)} A-share common-equity snapshots; excluded {len(excluded)} non-target securities.")
    return manifest


if __name__ == "__main__":
    generate_snapshots()
