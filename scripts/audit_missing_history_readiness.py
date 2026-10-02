#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

from a_share_agent.backtest.missing_history_readiness import audit_missing_history_candidates

ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "security_master_missing_history_readiness.csv"
OUT_JSON = ROOT / "security_master_missing_history_readiness_summary.json"


def main() -> int:
    audit = audit_missing_history_candidates(ROOT)
    rows = audit.pop("rows")

    fields = [
        "symbol",
        "membership_ready",
        "execution_ready",
        "listing_date",
        "delisting_date",
        "candidate_status",
        "membership_source_file",
        "membership_source_register_hash_match",
        "membership_source_record_found",
        "membership_source_semantic_status",
        "membership_source_allows_delisting_date",
        "membership_source_dates_match_candidate",
        "research_window_start",
        "research_window_end",
        "research_window_trading_days",
        "raw_filename",
        "raw_file_exists",
        "raw_manifest_hash_present",
        "raw_file_hash_match",
        "raw_dataset_hash_match",
        "daily_raw_coverage_fresh",
        "raw_expected_trading_days",
        "raw_present_trading_days",
        "raw_missing_trading_days",
        "raw_window_coverage",
        "raw_window_complete",
        "status_interval_count",
        "status_provenance_verified",
        "status_window_coverage_complete",
        "status_gap_day_count",
        "status_conflict_day_count",
        "sector_interval_count",
        "sector_provenance_verified",
        "sector_window_coverage_complete",
        "sector_gap_day_count",
        "sector_conflict_day_count",
        "blockers",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key) for key in fields}
            out["blockers"] = ";".join(row.get("blockers") or [])
            writer.writerow(out)

    audit["output_csv"] = OUT_CSV.name
    audit["safety_rule"] = (
        "membership_ready is independently re-derived from current hash-bound exchange source artifacts. "
        "execution_ready additionally requires complete research-window Raw/Status/Sector coverage. "
        "This audit never mutates security_master.csv."
    )
    OUT_JSON.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
