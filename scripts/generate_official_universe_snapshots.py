#!/usr/bin/env python3
"""Generate official_universe_snapshots/<date>.csv files from independent raw official registers.

Source files:
- data/backtest/official_universe_snapshots/raw_registers/sse_main_listing_register.csv
- data/backtest/official_universe_snapshots/raw_registers/star_listing_register.csv
- data/backtest/official_universe_snapshots/raw_registers/szse_listing_register.csv
- data/backtest/official_universe_snapshots/raw_registers/bse_listing_register.csv
- data/backtest/official_universe_snapshots/raw_registers/sse_delisted_register.csv
- data/backtest/official_universe_snapshots/raw_registers/szse_delisted_register.csv

This generator is COMPLETELY INDEPENDENT of security_master.csv.
"""

import csv
import json
import hashlib
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
RAW_REG_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
SNAPSHOT_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(filepath: Path) -> str:
    if not filepath.exists():
        return ""
    h = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_raw_official_registers():
    """Load authoritative listing and delisting registers directly from exchange exports."""
    official_stocks = {}

    # 1. SSE Main Board
    sse_file = RAW_REG_DIR / "sse_main_listing_register.csv"
    if sse_file.exists():
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
    if star_file.exists():
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
    if szse_file.exists():
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
    if bse_file.exists():
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

    # 5. Delisted SSE
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

    # 6. Delisted SZSE
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


def generate_snapshots():
    official_stocks = load_raw_official_registers()
    snapshot_dates = ["2024-10-08", "2025-09-29", "2026-07-01", "2026-08-31", "2026-09-30"]
    fields = ["date", "symbol", "exchange", "board", "listing_date", "source", "source_document_id_or_url", "dataset_version"]

    snapshot_stats = {}

    for d in snapshot_dates:
        records = []
        for sym, data in sorted(official_stocks.items()):
            ld = data["listing_date"]
            dd = data["delisting_date"]
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
    generate_snapshots()
