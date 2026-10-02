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
        "raw_filename",
        "raw_file_exists",
        "raw_manifest_hash_present",
        "raw_file_hash_match",
        "status_interval_count",
        "status_provenance_verified",
        "sector_interval_count",
        "sector_provenance_verified",
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
        "membership_ready is reference-data readiness only; execution_ready is required before "
        "strategy eligibility. This audit never mutates security_master.csv."
    )
    OUT_JSON.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
