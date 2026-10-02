#!/usr/bin/env python3
"""Compute exact DAILY Raw Bar Coverage for all trading days in the backtest period.

For every single trading date:
  eligible_symbols_on(date)
  raw_bar_available_on(date)
  daily_coverage(date) = raw_available / eligible

Outputs:
- daily_raw_coverage.csv
- Summary statistics: min_daily_raw_coverage, median_daily_raw_coverage, p05_daily_raw_coverage, days_below_98pct
"""

import csv
import json
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "backtest" / "raw_prices"
BM_FILE = ROOT / "data" / "backtest" / "cache" / "bars" / "000300_SH.json"
MASTER_FILE = ROOT / "data" / "backtest" / "security_master.csv"
OUTPUT_FILE = ROOT / "daily_raw_coverage.csv"


def compute_daily_raw_coverage():
    # 1. Trading dates
    if not BM_FILE.exists():
        bm_sz = ROOT / "data" / "backtest" / "cache" / "bars" / "000001_SZ.json"
        with bm_sz.open("r", encoding="utf-8") as f:
            bm_bars = json.load(f)
    else:
        with BM_FILE.open("r", encoding="utf-8") as f:
            bm_bars = json.load(f)
    trading_dates = sorted([b["date"] for b in bm_bars if "2024-10-01" <= b["date"] <= "2026-09-30"])

    # 2. Security master intervals
    with MASTER_FILE.open("r", encoding="utf-8-sig") as f:
        master = list(csv.DictReader(f))

    master_lookup = {}
    for r in master:
        s = r["symbol"]
        master_lookup[s] = {
            "board": r["board"],
            "start": r.get("active_from") or r.get("listing_date") or "",
            "end": r.get("active_to") or r.get("delisting_date") or "",
        }

    # 3. Read raw CSV bar dates
    print(f"Scanning raw price CSVs in {RAW_DIR}...")
    raw_dates_by_sym = {}
    for f in RAW_DIR.glob("*.csv"):
        sym = f.stem.replace("_", ".")
        dates_set = set()
        with f.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                d = row.get("date")
                if d:
                    dates_set.add(d)
        raw_dates_by_sym[sym] = dates_set
    print(f"Loaded raw price dates for {len(raw_dates_by_sym)} symbols.")

    # 4. Compute daily coverage
    daily_rows = []
    all_covs = []

    for d in trading_dates:
        eligible_by_board = defaultdict(int)
        available_by_board = defaultdict(int)
        tot_eligible = 0
        tot_available = 0

        for s, info in master_lookup.items():
            st = info["start"]
            ed = info["end"]
            if st and d < st:
                continue
            if ed and d > ed:
                continue
            b = info["board"]
            eligible_by_board[b] += 1
            tot_eligible += 1

            if s in raw_dates_by_sym and d in raw_dates_by_sym[s]:
                available_by_board[b] += 1
                tot_available += 1

        cov = tot_available / tot_eligible if tot_eligible else 0.0
        all_covs.append(cov)

        def pct_str(nom, denom):
            return f"{(nom / denom * 100):.2f}%" if denom else "0.00%"

        daily_rows.append({
            "date": d,
            "eligible_symbols": tot_eligible,
            "available_raw_bars": tot_available,
            "raw_coverage_pct": f"{cov * 100:.2f}%",
            "sse_main_cov": pct_str(available_by_board["SSE_MAIN"], eligible_by_board["SSE_MAIN"]),
            "star_cov": pct_str(available_by_board["STAR"], eligible_by_board["STAR"]),
            "szse_main_cov": pct_str(available_by_board["SZSE_MAIN"], eligible_by_board["SZSE_MAIN"]),
            "chinext_cov": pct_str(available_by_board["CHINEXT"], eligible_by_board["CHINEXT"]),
            "bse_cov": pct_str(available_by_board["BSE"], eligible_by_board["BSE"]),
        })

    p05 = sorted(all_covs)[int(len(all_covs) * 0.05)]
    median = statistics.median(all_covs)
    min_cov = min(all_covs)
    days_below_98 = sum(1 for c in all_covs if c < 0.98)

    summary = {
        "trading_days": len(trading_dates),
        "min_daily_raw_coverage": round(min_cov, 4),
        "median_daily_raw_coverage": round(median, 4),
        "p05_daily_raw_coverage": round(p05, 4),
        "days_below_98pct": days_below_98,
        "meets_formal_threshold": bool(min_cov >= 0.98 and days_below_98 == 0),
    }

    print(f"\nDaily Raw Bar Coverage Summary ({len(trading_dates)} trading days):")
    print(f"  Min Daily Coverage:    {min_cov * 100:.2f}%")
    print(f"  Median Daily Coverage: {median * 100:.2f}%")
    print(f"  P05 Daily Coverage:    {p05 * 100:.2f}%")
    print(f"  Days Below 98%:        {days_below_98} / {len(trading_dates)}")
    print(f"  Meets Formal 98% Gate: {summary['meets_formal_threshold']}")

    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date", "eligible_symbols", "available_raw_bars", "raw_coverage_pct",
            "sse_main_cov", "star_cov", "szse_main_cov", "chinext_cov", "bse_cov"
        ])
        w.writeheader()
        w.writerows(daily_rows)

    print(f"Generated {OUTPUT_FILE}.")
    return summary


if __name__ == "__main__":
    compute_daily_raw_coverage()
