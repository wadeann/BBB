from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .provenance_audit import (
    is_a_share_common_equity_symbol,
    local_record_active_on,
    read_csv_rows,
)


def snapshot_row_is_target(row: dict[str, str]) -> bool:
    """Return True only for target A-share common-equity snapshot rows.

    Explicit ``security_type`` labels fail closed when they are not
    ``A_SHARE_COMMON_EQUITY``. The board/code classifier is always enforced as a
    second guard, so an incorrectly labelled B share/CDR cannot enter the official
    set merely because its security_type string says A-share common equity.
    """
    symbol = str(row.get("symbol") or "").strip()
    board = str(row.get("board") or "").strip().upper()
    security_type = str(row.get("security_type") or "").strip().upper()
    if not symbol or not is_a_share_common_equity_symbol(symbol, board):
        return False
    if security_type and security_type != "A_SHARE_COMMON_EQUITY":
        return False
    return True


def master_rows_by_symbol(master_rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in master_rows:
        symbol = str(row.get("symbol") or "").strip()
        board = str(row.get("board") or "").strip().upper()
        if symbol and is_a_share_common_equity_symbol(symbol, board):
            grouped[symbol].append(row)
    return dict(grouped)


def universe_sets_for_date(
    master_rows: list[dict[str, str]],
    snapshot_rows: list[dict[str, str]],
    audit_date: str,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Build official and local target-A-share sets with identical semantics.

    Local membership is listing/delisting interval membership only. ST, suspension,
    strategy eligibility and Raw-bar availability are intentionally irrelevant here:
    this function answers whether the security belongs to the listed A-share universe
    on the audit date, not whether the strategy may trade it that day.
    """
    official_rows = {
        str(row.get("symbol") or "").strip(): row
        for row in snapshot_rows
        if snapshot_row_is_target(row)
    }

    local_active: dict[str, dict[str, str]] = {}
    for symbol, candidates in master_rows_by_symbol(master_rows).items():
        for candidate in candidates:
            if local_record_active_on(candidate, audit_date):
                local_active[symbol] = candidate
                break
    return official_rows, local_active


def reconcile_universe_snapshot_counts(master_path: Path, snapshot_dir: Path) -> dict[str, Any]:
    """Reconcile all dated official snapshots against the local security master.

    Counts are summed per snapshot, matching the detailed provenance audit output.
    This helper is the single set-construction source used by Preflight and tests.
    """
    master_rows = read_csv_rows(master_path)
    missing_total = 0
    extra_total = 0
    snapshot_count = 0
    per_snapshot: list[dict[str, Any]] = []

    for snap in sorted(snapshot_dir.glob("20??-??-??.csv")):
        date = snap.stem
        official_rows, local_active = universe_sets_for_date(master_rows, read_csv_rows(snap), date)
        official_set = set(official_rows)
        local_set = set(local_active)
        missing = sorted(official_set - local_set)
        extra = sorted(local_set - official_set)
        missing_total += len(missing)
        extra_total += len(extra)
        snapshot_count += 1
        per_snapshot.append(
            {
                "date": date,
                "official_count": len(official_set),
                "local_count": len(local_set),
                "missing_count": len(missing),
                "extra_count": len(extra),
                "missing_symbols": missing,
                "extra_symbols": extra,
            }
        )

    return {
        "snapshot_count": snapshot_count,
        "missing_total": missing_total,
        "extra_total": extra_total,
        "match": bool(snapshot_count and missing_total == 0 and extra_total == 0),
        "per_snapshot": per_snapshot,
    }
