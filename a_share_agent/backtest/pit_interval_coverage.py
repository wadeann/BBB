from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .provenance_audit import (
    audit_interval_provenance,
    is_a_share_common_equity_symbol,
    local_record_active_on,
    read_csv_rows,
)


def _interval_active_on(row: dict[str, str], audit_date: str) -> bool:
    start = str(row.get("effective_from") or row.get("start_date") or "").strip()
    end = str(row.get("effective_to") or row.get("end_date") or "").strip()
    if start and audit_date < start:
        return False
    if end and audit_date > end:
        return False
    return True


def _value(row: dict[str, str], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value.upper()
    return ""


def audit_pit_interval_coverage(
    master_path: Path,
    interval_path: Path,
    provenance_path: Path,
    trading_dates: list[str],
    *,
    value_fields: tuple[str, ...],
    min_provenance_coverage: float = 0.95,
    source_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit full-window PIT interval coverage over the active A-share universe.

    Row-level provenance and temporal coverage are intentionally separate concepts.
    A dataset is complete only when its source provenance passes *and* every active
    A-share symbol has exactly one verified, non-empty interval value on every trusted
    trading date. Missing days, overlapping intervals, conflicting values, or intervals
    backed only by unverified source IDs fail closed.
    """
    source_audit = source_audit or audit_interval_provenance(
        interval_path,
        provenance_path,
        min_coverage=min_provenance_coverage,
    )
    all_source_ids = set((source_audit.get("interval_count_by_source") or {}).keys())
    unverified_source_ids = set(source_audit.get("unverified_source_ids") or [])
    verified_source_ids = {x for x in all_source_ids - unverified_source_ids if x and x != "<EMPTY_SOURCE>"}

    master_by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv_rows(master_path):
        symbol = str(row.get("symbol") or "").strip()
        board = str(row.get("board") or "").strip().upper()
        if symbol and is_a_share_common_equity_symbol(symbol, board):
            master_by_symbol[symbol].append(row)

    intervals_by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv_rows(interval_path):
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            intervals_by_symbol[symbol].append(row)

    dates = sorted({str(d).strip() for d in trading_dates if str(d).strip()})
    expected_symbol_days = 0
    covered_symbol_days = 0
    missing_symbol_days = 0
    unverified_symbol_days = 0
    overlap_symbol_days = 0
    conflict_symbol_days = 0
    empty_value_symbol_days = 0
    per_date: list[dict[str, Any]] = []

    missing_samples: list[dict[str, str]] = []
    conflict_samples: list[dict[str, str]] = []
    unverified_samples: list[dict[str, str]] = []

    for audit_date in dates:
        active_symbols = [
            symbol
            for symbol, records in master_by_symbol.items()
            if any(local_record_active_on(record, audit_date) for record in records)
        ]
        date_expected = len(active_symbols)
        date_covered = 0
        date_missing = 0
        date_unverified = 0
        date_conflicts = 0
        date_overlaps = 0
        date_empty = 0

        for symbol in active_symbols:
            expected_symbol_days += 1
            matching = [row for row in intervals_by_symbol.get(symbol, []) if _interval_active_on(row, audit_date)]
            if not matching:
                missing_symbol_days += 1
                date_missing += 1
                if len(missing_samples) < 50:
                    missing_samples.append({"date": audit_date, "symbol": symbol})
                continue

            if len(matching) > 1:
                overlap_symbol_days += 1
                date_overlaps += 1

            verified_rows = [
                row for row in matching
                if str(row.get("source") or "").strip() in verified_source_ids
            ]
            if not verified_rows:
                unverified_symbol_days += 1
                date_unverified += 1
                if len(unverified_samples) < 50:
                    unverified_samples.append({"date": audit_date, "symbol": symbol})
                continue

            values = {_value(row, value_fields) for row in verified_rows}
            values.discard("")
            if not values:
                empty_value_symbol_days += 1
                date_empty += 1
                continue
            if len(values) != 1 or len(verified_rows) != 1:
                conflict_symbol_days += 1
                date_conflicts += 1
                if len(conflict_samples) < 50:
                    conflict_samples.append(
                        {"date": audit_date, "symbol": symbol, "values": "|".join(sorted(values))}
                    )
                continue

            covered_symbol_days += 1
            date_covered += 1

        per_date.append(
            {
                "date": audit_date,
                "active_symbols": date_expected,
                "covered_symbol_days": date_covered,
                "missing_symbol_days": date_missing,
                "unverified_symbol_days": date_unverified,
                "overlap_symbol_days": date_overlaps,
                "conflict_symbol_days": date_conflicts,
                "empty_value_symbol_days": date_empty,
                "pit_coverage": round(date_covered / date_expected, 6) if date_expected else 0.0,
            }
        )

    temporal_coverage = covered_symbol_days / expected_symbol_days if expected_symbol_days else 0.0
    min_daily_coverage = min((row["pit_coverage"] for row in per_date), default=0.0)
    complete = bool(
        dates
        and expected_symbol_days
        and source_audit.get("dataset_complete")
        and covered_symbol_days == expected_symbol_days
        and missing_symbol_days == 0
        and unverified_symbol_days == 0
        and overlap_symbol_days == 0
        and conflict_symbol_days == 0
        and empty_value_symbol_days == 0
    )
    return {
        "trading_date_count": len(dates),
        "expected_symbol_days": expected_symbol_days,
        "covered_symbol_days": covered_symbol_days,
        "missing_symbol_days": missing_symbol_days,
        "unverified_symbol_days": unverified_symbol_days,
        "overlap_symbol_days": overlap_symbol_days,
        "conflict_symbol_days": conflict_symbol_days,
        "empty_value_symbol_days": empty_value_symbol_days,
        "temporal_coverage": round(temporal_coverage, 6),
        "min_daily_pit_coverage": round(min_daily_coverage, 6),
        "dataset_complete": complete,
        "verified_source_id_count": len(verified_source_ids),
        "missing_samples": missing_samples,
        "unverified_samples": unverified_samples,
        "conflict_samples": conflict_samples,
        "per_date": per_date,
        "source_provenance_audit": source_audit,
    }
