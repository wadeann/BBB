#!/usr/bin/env python3
"""Reconcile production corporate actions against an independent, hashed official event set.

The official register is never constructed from production data. v0.7.6 additionally
requires row-level provenance: every official event must carry a concrete document/url
and the SHA256 of a raw source artifact declared in the official manifest.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from a_share_agent.backtest.provenance_audit import reconcile_corporate_action_sets

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "data" / "backtest"
PROD_CA_FILE = BACKTEST / "corporate_actions.csv"
OFFICIAL_CA_FILE = BACKTEST / "official_corporate_actions_register.csv"
OFFICIAL_CA_MANIFEST = BACKTEST / "official_corporate_actions_manifest.json"
DIFF_FILE = ROOT / "corporate_action_set_diff.csv"


def _write_diff(metrics: dict) -> None:
    fields = ["status", "symbol", "action_type", "ex_date", "record_date", "detail"]
    rows: list[dict[str, str]] = []
    if not metrics.get("official_register_valid"):
        validation = metrics.get("official_register_validation") or {}
        rows.append(
            {
                "status": "OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED",
                "detail": str(validation.get("reason") or "unknown"),
            }
        )
    else:
        for status, key_name in (
            ("MISSING_IN_PRODUCTION", "missing_keys"),
            ("EXTRA_IN_PRODUCTION", "extra_keys"),
            ("VALUE_CONFLICT", "conflicting_keys"),
        ):
            for key in metrics.get(key_name) or []:
                rows.append(
                    {
                        "status": status,
                        "symbol": key[0],
                        "action_type": key[1],
                        "ex_date": key[2],
                        "record_date": key[3],
                        "detail": "independent event-set reconciliation",
                    }
                )
    with DIFF_FILE.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def reconcile_corporate_actions() -> dict:
    metrics = reconcile_corporate_action_sets(PROD_CA_FILE, OFFICIAL_CA_FILE, OFFICIAL_CA_MANIFEST)
    _write_diff(metrics)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return metrics


if __name__ == "__main__":
    reconcile_corporate_actions()
