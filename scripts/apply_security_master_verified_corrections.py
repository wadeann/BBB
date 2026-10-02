#!/usr/bin/env python3
"""Apply narrowly reviewed security-master corrections backed by official snapshots.

This tool is deliberately conservative.  It can only clear a local delisting/active_to
value that predates an independently generated official A-share listing snapshot in
which the same security is still present.  It never invents a future delisting date,
never adds a symbol, and never changes strategy/tradability fields.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from a_share_agent.backtest.provenance_audit import read_csv_rows
from a_share_agent.backtest.universe_reconciliation import snapshot_row_is_target

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER = ROOT / "data" / "backtest" / "security_master.csv"
DEFAULT_CORRECTIONS = ROOT / "data" / "backtest" / "security_master_verified_corrections.csv"
DEFAULT_SNAPSHOTS = ROOT / "data" / "backtest" / "official_universe_snapshots"
DEFAULT_AUDIT = ROOT / "security_master_correction_audit.csv"
ALLOWED_ACTION = "CLEAR_FALSE_EARLY_DELISTING"


def _snapshot_symbols(snapshot_dir: Path, date: str) -> set[str]:
    path = snapshot_dir / f"{date}.csv"
    if not path.exists():
        raise RuntimeError(f"evidence snapshot missing: {path}")
    return {
        str(row.get("symbol") or "").strip()
        for row in read_csv_rows(path)
        if snapshot_row_is_target(row)
    }


def _clear_if_early(value: str, verified_active_through: str) -> tuple[str, bool]:
    value = str(value or "").strip()
    if value and value < verified_active_through:
        return "", True
    return value, False


def apply_corrections(
    master_path: Path,
    corrections_path: Path,
    snapshot_dir: Path,
    *,
    apply: bool,
    audit_path: Path,
) -> dict[str, Any]:
    master_rows = read_csv_rows(master_path)
    corrections = read_csv_rows(corrections_path)
    if not master_rows:
        raise RuntimeError(f"security master missing/empty: {master_path}")
    if not corrections:
        raise RuntimeError(f"verified correction set missing/empty: {corrections_path}")

    by_symbol: dict[str, list[int]] = {}
    for idx, row in enumerate(master_rows):
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            by_symbol.setdefault(symbol, []).append(idx)

    snapshot_cache: dict[str, set[str]] = {}
    audit_rows: list[dict[str, str]] = []
    changed = 0

    for correction in corrections:
        symbol = str(correction.get("symbol") or "").strip()
        action = str(correction.get("action") or "").strip()
        verified_through = str(correction.get("verified_active_through") or "").strip()
        snapshot_date = str(correction.get("evidence_snapshot_date") or "").strip()
        if not symbol or action != ALLOWED_ACTION or not verified_through or not snapshot_date:
            raise RuntimeError(f"invalid correction row: {correction}")
        if verified_through != snapshot_date:
            raise RuntimeError(f"correction must bind active-through date to snapshot date: {symbol}")
        if snapshot_date not in snapshot_cache:
            snapshot_cache[snapshot_date] = _snapshot_symbols(snapshot_dir, snapshot_date)
        if symbol not in snapshot_cache[snapshot_date]:
            raise RuntimeError(f"official target-A-share snapshot does not contain {symbol} on {snapshot_date}")

        indexes = by_symbol.get(symbol, [])
        if len(indexes) != 1:
            raise RuntimeError(f"expected exactly one master row for {symbol}, found {len(indexes)}")
        row = master_rows[indexes[0]]
        before_active_to = str(row.get("active_to") or "").strip()
        before_delisting = str(row.get("delisting_date") or "").strip()
        new_active_to, changed_active_to = _clear_if_early(before_active_to, verified_through)
        new_delisting, changed_delisting = _clear_if_early(before_delisting, verified_through)

        if changed_active_to:
            row["active_to"] = new_active_to
        if changed_delisting:
            row["delisting_date"] = new_delisting
        row_changed = changed_active_to or changed_delisting
        changed += int(row_changed)
        audit_rows.append(
            {
                "symbol": symbol,
                "status": "CORRECTED" if row_changed else "ALREADY_CONSISTENT",
                "before_active_to": before_active_to,
                "after_active_to": str(row.get("active_to") or ""),
                "before_delisting_date": before_delisting,
                "after_delisting_date": str(row.get("delisting_date") or ""),
                "verified_active_through": verified_through,
                "evidence_snapshot_date": snapshot_date,
                "evidence_source": str(correction.get("evidence_source") or ""),
                "evidence_document_id_or_url": str(correction.get("evidence_document_id_or_url") or ""),
                "reason": str(correction.get("reason") or ""),
            }
        )

    audit_fields = [
        "symbol", "status", "before_active_to", "after_active_to",
        "before_delisting_date", "after_delisting_date", "verified_active_through",
        "evidence_snapshot_date", "evidence_source", "evidence_document_id_or_url", "reason",
    ]
    with audit_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=audit_fields)
        writer.writeheader()
        writer.writerows(audit_rows)

    if apply and changed:
        fields = list(master_rows[0].keys())
        with master_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(master_rows)

    return {
        "correction_rows": len(corrections),
        "changed_rows": changed,
        "applied": bool(apply),
        "audit_path": str(audit_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--apply", action="store_true", help="write validated corrections into security_master.csv")
    args = parser.parse_args()
    result = apply_corrections(
        args.master,
        args.corrections,
        args.snapshots,
        apply=args.apply,
        audit_path=args.audit,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
