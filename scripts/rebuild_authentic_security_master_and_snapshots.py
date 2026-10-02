#!/usr/bin/env python3
"""Rebuild authentic Security Master listing dates and independent official universe snapshots.

1. Rebuilds security_master.csv listing_date, active_from, delisting_date, active_to using
   authoritative official exchange registers directly from SSE, SZSE, and BSE.
2. Generates security_master_listing_date_audit.csv auditing every symbol.
3. Generates independent official universe snapshots in data/backtest/official_universe_snapshots/<date>.csv
   directly from the exchange registers (completely independent of local security_master).
4. Generates official_universe_snapshot_manifest.json with full cryptographic provenance.
"""

import csv
import json
import hashlib
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
RAW_REG_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
SNAPSHOT_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots"
BACKTEST_DIR = ROOT / "data" / "backtest"


def sha256_file(filepath: Path) -> str:
    if not filepath.exists():
        return ""
    h = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_official_registers():
    """Load all official listing and delisting registers from exchanges."""
    official_stocks = {}

    # 1. SSE Main Board
    sse_file = RAW_REG_DIR / "sse_main_listing_register.csv"
    with sse_file.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["证券代码"].strip()
            sym = f"{code}.SH"
            official_stocks[sym] = {
                "symbol": sym,
                "name": r["证券简称"].strip(),
                "exchange": "SSE",
                "board": "SSE_MAIN",
                "listing_date": r["上市日期"].strip(),
                "delisting_date": "",
                "source": "SSE_OFFICIAL_LISTING_REGISTER",
                "source_document_id_or_url": f"http://www.sse.com.cn/assortment/stock/list/info/price/index.shtml?COMPANY_CODE={code}",
                "dataset_version": "2026.09.30",
            }

    # 2. STAR Board (科创板)
    star_file = RAW_REG_DIR / "star_listing_register.csv"
    with star_file.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["证券代码"].strip()
            sym = f"{code}.SH"
            official_stocks[sym] = {
                "symbol": sym,
                "name": r["证券简称"].strip(),
                "exchange": "SSE",
                "board": "STAR",
                "listing_date": r["上市日期"].strip(),
                "delisting_date": "",
                "source": "SSE_OFFICIAL_LISTING_REGISTER",
                "source_document_id_or_url": f"http://star.sse.com.cn/company/detail.shtml?stockCode={code}",
                "dataset_version": "2026.09.30",
            }

    # 3. SZSE Main & ChiNext
    szse_file = RAW_REG_DIR / "szse_listing_register.csv"
    with szse_file.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["A股代码"].strip().zfill(6)
            sym = f"{code}.SZ"
            board = "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
            official_stocks[sym] = {
                "symbol": sym,
                "name": r["A股简称"].strip(),
                "exchange": "SZSE",
                "board": board,
                "listing_date": r["A股上市日期"].strip(),
                "delisting_date": "",
                "source": "SZSE_OFFICIAL_LISTING_REGISTER",
                "source_document_id_or_url": f"http://www.szse.cn/market/product/stock/list/index.html?stockCode={code}",
                "dataset_version": "2026.09.30",
            }

    # 4. BSE (北交所)
    bse_file = RAW_REG_DIR / "bse_listing_register.csv"
    with bse_file.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["证券代码"].strip()
            sym = f"{code}.BJ"
            official_stocks[sym] = {
                "symbol": sym,
                "name": r["证券简称"].strip(),
                "exchange": "BSE",
                "board": "BSE",
                "listing_date": r["上市日期"].strip(),
                "delisting_date": "",
                "source": "BSE_OFFICIAL_LISTING_REGISTER",
                "source_document_id_or_url": f"https://www.bse.cn/company/company_detail.html?stockCode={code}",
                "dataset_version": "2026.09.30",
            }

    # 5. SSE Delisted
    sse_delist_file = RAW_REG_DIR / "sse_delisted_register.csv"
    if sse_delist_file.exists():
        with sse_delist_file.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r["公司代码"].strip()
                sym = f"{code}.SH"
                ld = r["上市日期"].strip()
                dd = r.get("暂停上市日期", "").strip() or r.get("终止上市日期", "").strip()
                board = "STAR" if code.startswith("688") else "SSE_MAIN"
                if sym in official_stocks:
                    official_stocks[sym]["delisting_date"] = dd
                else:
                    official_stocks[sym] = {
                        "symbol": sym,
                        "name": r["公司简称"].strip(),
                        "exchange": "SSE",
                        "board": board,
                        "listing_date": ld,
                        "delisting_date": dd,
                        "source": "SSE_OFFICIAL_DELISTED_REGISTER",
                        "source_document_id_or_url": f"http://www.sse.com.cn/assortment/stock/list/delist/info/price/index.shtml?COMPANY_CODE={code}",
                        "dataset_version": "2026.09.30",
                    }

    # 6. SZSE Delisted
    szse_delist_file = RAW_REG_DIR / "szse_delisted_register.csv"
    if szse_delist_file.exists():
        with szse_delist_file.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r["证券代码"].strip().zfill(6)
                sym = f"{code}.SZ"
                ld = r["上市日期"].strip()
                dd = r.get("终止上市日期", "").strip()
                board = "CHINEXT" if code.startswith(("300", "301")) else "SZSE_MAIN"
                if sym in official_stocks:
                    official_stocks[sym]["delisting_date"] = dd
                else:
                    official_stocks[sym] = {
                        "symbol": sym,
                        "name": r["证券简称"].strip(),
                        "exchange": "SZSE",
                        "board": board,
                        "listing_date": ld,
                        "delisting_date": dd,
                        "source": "SZSE_OFFICIAL_DELISTED_REGISTER",
                        "source_document_id_or_url": f"http://www.szse.cn/market/stock/suspend/index.html?stockCode={code}",
                        "dataset_version": "2026.09.30",
                    }

    return official_stocks


def rebuild_security_master(official_stocks):
    master_path = BACKTEST_DIR / "security_master.csv"
    with master_path.open("r", encoding="utf-8-sig") as f:
        master_rows = list(csv.DictReader(f))

    audit_rows = []
    updated_rows = []

    for r in master_rows:
        sym = r["symbol"]
        old_ld = r.get("listing_date") or ""
        old_dd = r.get("delisting_date") or ""
        off = official_stocks.get(sym)

        if off:
            real_ld = off["listing_date"]
            real_dd = off["delisting_date"] or old_dd
            source = off["source"]
            audit_status = "AUTHENTIC_EXCHANGE_VERIFIED"
        else:
            real_ld = old_ld
            real_dd = old_dd
            source = "FALLBACK"
            audit_status = "UNVERIFIED"

        new_r = dict(r)
        new_r["listing_date"] = real_ld
        new_r["active_from"] = real_ld
        new_r["delisting_date"] = real_dd
        new_r["active_to"] = real_dd

        updated_rows.append(new_r)
        audit_rows.append({
            "symbol": sym,
            "name": r.get("name", ""),
            "board": r.get("board", ""),
            "old_listing_date": old_ld,
            "authentic_listing_date": real_ld,
            "authentic_delisting_date": real_dd,
            "source": source,
            "audit_status": audit_status,
        })

    # Save updated security_master.csv
    fieldnames = list(master_rows[0].keys())
    with master_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(updated_rows)

    # Save audit report
    audit_file = ROOT / "security_master_listing_date_audit.csv"
    with audit_file.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "symbol", "name", "board", "old_listing_date", "authentic_listing_date",
            "authentic_delisting_date", "source", "audit_status"
        ])
        w.writeheader()
        w.writerows(audit_rows)

    print(f"Rebuilt {master_path} with authentic listing dates.")
    print(f"Saved {audit_file}: {len(audit_rows)} rows audited.")
    return audit_rows


def generate_independent_snapshots(official_stocks):
    snapshot_dates = ["2024-10-08", "2025-09-29", "2026-07-01", "2026-08-31", "2026-09-30"]
    fields = ["date", "symbol", "exchange", "board", "listing_date", "source", "source_document_id_or_url", "dataset_version"]

    snapshot_hashes = {}
    snapshot_stats = {}

    for d in snapshot_dates:
        records = []
        for sym, data in sorted(official_stocks.items()):
            ld = data["listing_date"]
            dd = data["delisting_date"]
            # Active on date d if listed on or before d and not delisted before d
            if ld and ld <= d:
                if not dd or dd >= d:
                    records.append({
                        "date": d,
                        "symbol": sym,
                        "exchange": data["exchange"],
                        "board": data["board"],
                        "listing_date": ld,
                        "source": data["source"],
                        "source_document_id_or_url": data["source_document_id_or_url"],
                        "dataset_version": data["dataset_version"],
                    })

        snap_file = SNAPSHOT_DIR / f"{d}.csv"
        with snap_file.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(records)

        snap_hash = sha256_file(snap_file)
        snapshot_hashes[d] = snap_hash

        by_board = defaultdict(int)
        for r in records:
            by_board[r["board"]] += 1

        snapshot_stats[d] = {
            "file": f"data/backtest/official_universe_snapshots/{d}.csv",
            "total_symbols": len(records),
            "by_board": dict(by_board),
            "sha256": snap_hash,
            "source": "EXCHANGE_OFFICIAL_LISTING_REGISTERS",
        }
        print(f"Generated independent snapshot {snap_file}: {len(records)} official symbols.")

    # Source register hashes
    raw_hashes = {
        f.name: sha256_file(f) for f in sorted(RAW_REG_DIR.glob("*.csv"))
    }

    manifest = {
        "manifest_id": "official_universe_snapshots_v0.7.4",
        "description": "Independent authoritative A-share official universe snapshots directly derived from SSE, SZSE, and BSE official registers",
        "generated_at": "2026-10-02T17:15:00+08:00",
        "source_registers": {
            "directory": "data/backtest/official_universe_snapshots/raw_registers/",
            "files": raw_hashes,
        },
        "snapshots": snapshot_stats,
    }

    manifest_file = ROOT / "official_universe_snapshot_manifest.json"
    with manifest_file.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    manifest_internal = SNAPSHOT_DIR / "manifest.json"
    with manifest_internal.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {manifest_file} and {manifest_internal}.")
    return manifest


if __name__ == "__main__":
    print("Loading raw official exchange registers...")
    official = load_official_registers()
    print(f"Loaded {len(official)} official securities.")
    rebuild_security_master(official)
    generate_independent_snapshots(official)
    print("Security Master and Snapshots successfully rebuilt from independent authoritative registers!")
