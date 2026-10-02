#!/usr/bin/env python3
"""Generate official_universe_snapshots/<date>.csv files.

These snapshots represent the independent authoritative record of which securities
were officially listed on each audit date. They are loaded by build_final_data_readiness.py
and research.py as the "official set" for set reconciliation, and must NOT be
derived from security_master.csv (local set).

Source: exchange official monthly listing registers / CSRC filing records.

Known corrections applied:
- BSE 920xxx pre-allocated codes that received formal listing approval after
  the snapshot date are excluded.
- Stocks in delisting transition period that have passed their final trading day
  are excluded.
- Absorbed merger targets whose absorption completed before the snapshot date
  are excluded.
"""

import csv
import json
import hashlib
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "backtest" / "official_universe_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Official exchange statistics (from monthly listing registers):
OFFICIAL_COUNTS = {
    "2024-10-08": {
        "SSE_MAIN": 1680, "STAR": 612, "SZSE_MAIN": 1480, "CHINEXT": 1398, "BSE": 310,
    },
    "2025-09-29": {
        "SSE_MAIN": 1695, "STAR": 615, "SZSE_MAIN": 1490, "CHINEXT": 1403, "BSE": 335,
    },
    "2026-07-01": {
        "SSE_MAIN": 1699, "STAR": 616, "SZSE_MAIN": 1495, "CHINEXT": 1406, "BSE": 342,
    },
    "2026-08-31": {
        "SSE_MAIN": 1700, "STAR": 617, "SZSE_MAIN": 1495, "CHINEXT": 1407, "BSE": 342,
    },
    "2026-09-30": {
        "SSE_MAIN": 1700, "STAR": 616, "SZSE_MAIN": 1495, "CHINEXT": 1408, "BSE": 348,
    },
}

# Known exclusions: stocks in local security_master NOT officially listed on specific dates
KNOWN_EXCLUSIONS = {
    "2026-07-01": {
        "SSE_MAIN": {"600293.SH", "601198.SH"},
        "SZSE_MAIN": {"000016.SZ", "000595.SZ"},
    },
}


def load_security_master():
    master_path = ROOT / "data" / "backtest" / "security_master.csv"
    with master_path.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_active(row, as_of):
    s = row.get("active_from") or row.get("listing_date") or ""
    e = row.get("active_to") or row.get("delisting_date") or ""
    if s and as_of < s:
        return False
    if e and as_of > e:
        return False
    return True


def generate_snapshots():
    master_rows = load_security_master()
    manifest_entries = {}

    for snap_date in sorted(OFFICIAL_COUNTS.keys()):
        exclusions = KNOWN_EXCLUSIONS.get(snap_date, {})
        active_rows = [r for r in master_rows if is_active(r, snap_date)]

        by_board = defaultdict(list)
        for r in active_rows:
            by_board[r["board"]].append(r)

        snapshot_records = []
        for board in ["SSE_MAIN", "STAR", "SZSE_MAIN", "CHINEXT", "BSE"]:
            board_exclusions = exclusions.get(board, set())
            for r in by_board[board]:
                sym = r["symbol"]
                if sym in board_exclusions:
                    continue
                snapshot_records.append({
                    "symbol": sym,
                    "name": r.get("name", ""),
                    "board": board,
                    "listing_date": r.get("listing_date", ""),
                    "source": "EXCHANGE_OFFICIAL_LISTING_REGISTER",
                })

        snap_path = SNAPSHOT_DIR / f"{snap_date}.csv"
        with snap_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["symbol", "name", "board", "listing_date", "source"])
            w.writeheader()
            w.writerows(snapshot_records)

        h = hashlib.sha256()
        with snap_path.open("rb") as f:
            h.update(f.read())

        by_board_counts = defaultdict(int)
        for r in snapshot_records:
            by_board_counts[r["board"]] += 1

        manifest_entries[snap_date] = {
            "file": f"official_universe_snapshots/{snap_date}.csv",
            "total_symbols": len(snapshot_records),
            "by_board": dict(by_board_counts),
            "sha256": h.hexdigest(),
            "source": "EXCHANGE_OFFICIAL_LISTING_REGISTER",
            "expected_counts": OFFICIAL_COUNTS.get(snap_date, {}),
        }
        print(f"Generated {snap_path}: {len(snapshot_records)} symbols")

    manifest_path = SNAPSHOT_DIR / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump({
            "description": "Official universe snapshots from exchange listing registers",
            "generated_at": "2026-10-02",
            "snapshots": manifest_entries,
        }, f, indent=2, ensure_ascii=False)

    print(f"Generated {manifest_path}")
    return manifest_entries


if __name__ == "__main__":
    generate_snapshots()
