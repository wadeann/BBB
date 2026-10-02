"""v0.7.6 fail-closed research preflight wrapper.

The v0.7.4 research implementation is retained in ``research_legacy``. This wrapper
adds non-bypassable provenance checks around official universe snapshots, corporate
actions, status/sector interval sources, Raw OHLCV fingerprints, and coverage binding.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from . import research_legacy as _legacy
from .data import HistoricalDataProvider
from .data_integrity import verify_coverage_binding, verify_raw_dataset_manifest
from .provenance_audit import (
    audit_interval_provenance,
    is_a_share_common_equity_symbol,
    reconcile_corporate_action_sets,
)
from .service import settings_from

ResearchLab = _legacy.ResearchLab
research_validity = _legacy.research_validity
_metric_row = _legacy._metric_row
_neutral_sector_share = _legacy._neutral_sector_share
_ORIGINAL_PREFLIGHT = _legacy.run_research_preflight


def _is_a_share_common_equity_symbol(symbol: str, board: str) -> bool:
    """Compatibility wrapper retained for existing tests/imports."""
    return is_a_share_common_equity_symbol(symbol, board)


def _official_universe_audit(config, mcp, overrides: dict[str, Any] | None) -> tuple[bool, int, int]:
    settings = settings_from(config, overrides or {})
    provider = HistoricalDataProvider(config.project_root, mcp, use_cache=settings.cache)
    universe = provider.load_universe_for_period(
        settings.start_date,
        settings.end_date,
        settings.universe_file,
        max_universe=settings.max_universe,
        mode=settings.universe_mode,
    )
    snapshot_dir = config.project_root / "data" / "backtest" / "official_universe_snapshots"
    snapshots = sorted(snapshot_dir.glob("*.csv")) if snapshot_dir.exists() else []
    if not snapshots:
        return False, 0, 0
    extra = missing = checked = 0
    for snap in snapshots:
        date = snap.stem
        if len(date) != 10 or not date.startswith("20"):
            continue
        official: set[str] = set()
        with snap.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                symbol = str(row.get("symbol") or "").strip()
                board = str(row.get("board") or "").strip().upper()
                security_type = str(row.get("security_type") or "").strip().upper()
                if security_type:
                    if security_type != "A_SHARE_COMMON_EQUITY":
                        continue
                elif not _is_a_share_common_equity_symbol(symbol, board):
                    continue
                if symbol:
                    official.add(symbol)
        active = set(provider.active_symbols_on(date, universe.symbols))
        extra += len(active - official)
        missing += len(official - active)
        checked += 1
    return bool(checked and extra == 0 and missing == 0), extra, missing


def _corporate_action_audit(root: Path) -> dict[str, Any]:
    return reconcile_corporate_action_sets(
        root / "data" / "backtest" / "corporate_actions.csv",
        root / "data" / "backtest" / "official_corporate_actions_register.csv",
        root / "data" / "backtest" / "official_corporate_actions_manifest.json",
    )


def _daily_coverage_audit(root: Path, actual_raw_hash: str | None) -> dict[str, Any]:
    csv_path = root / "daily_raw_coverage.csv"
    manifest_path = root / "daily_raw_coverage_manifest.json"
    binding = verify_coverage_binding(csv_path, manifest_path, actual_raw_hash)
    values: list[float] = []
    board_values = {k: [] for k in ("SSE_MAIN", "STAR", "SZSE_MAIN", "CHINEXT", "BSE")}
    colmap = {
        "SSE_MAIN": "sse_main_cov",
        "STAR": "star_cov",
        "SZSE_MAIN": "szse_main_cov",
        "CHINEXT": "chinext_cov",
        "BSE": "bse_cov",
    }
    if binding.get("daily_raw_coverage_fresh") and csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                raw = str(row.get("active_raw_coverage") or row.get("raw_coverage_pct") or "").rstrip("%")
                try:
                    value = float(raw)
                    values.append(value / 100.0 if value > 1 else value)
                except ValueError:
                    continue
                for board, column in colmap.items():
                    try:
                        x = float(str(row.get(column) or "").rstrip("%"))
                        board_values[board].append(x / 100.0 if x > 1 else x)
                    except ValueError:
                        pass
    ordered = sorted(values)
    return {
        "binding": binding,
        "fresh": bool(binding.get("daily_raw_coverage_fresh")),
        "min": ordered[0] if ordered else 0.0,
        "median": ordered[len(ordered) // 2] if ordered else 0.0,
        "p05": ordered[max(0, int(len(ordered) * 0.05))] if ordered else 0.0,
        "days_below_98pct": sum(1 for value in values if value < 0.98),
        "days": len(values),
        "by_exchange": {board: (min(board_values[board]) if board_values[board] else 0.0) for board in board_values},
    }


def run_research_preflight(config, mcp, *, overrides: dict[str, Any] | None = None, sample_size: int = 30) -> dict[str, Any]:
    result = _ORIGINAL_PREFLIGHT(config, mcp, overrides=overrides, sample_size=sample_size)
    root = config.project_root

    universe_match, extra, missing = _official_universe_audit(config, mcp, overrides)
    result["official_universe_set_match"] = universe_match
    result["universe_extra_symbol_count"] = extra
    result["universe_missing_symbol_count"] = missing
    detailed_diff = root / "official_universe_set_diff_detailed.csv"
    result["universe_detailed_diff_file"] = detailed_diff.name if detailed_diff.exists() else None

    ca = _corporate_action_audit(root)
    result["official_corporate_action_register_valid"] = ca["official_register_valid"]
    result["corporate_action_expected_vs_loaded"] = ca
    result["corporate_action_source_coverage"] = ca["ratio"]
    result["corporate_action_dataset_complete"] = ca["complete"]
    result["corporate_action_ready"] = ca["complete"]
    result["synthetic_corporate_actions_detected"] = ca["synthetic_events"]

    status = audit_interval_provenance(
        root / "data" / "backtest" / "historical_status_intervals.csv",
        root / "data" / "backtest" / "status_provenance.csv",
    )
    sector = audit_interval_provenance(
        root / "data" / "backtest" / "historical_sector_intervals.csv",
        root / "data" / "backtest" / "sector_provenance.csv",
    )
    result["status_provenance_audit"] = status
    result["status_source_coverage"] = status["source_coverage"]
    result["status_dataset_complete"] = status["dataset_complete"]
    result["sector_provenance_audit"] = sector
    result["sector_source_coverage"] = sector["source_coverage"]
    result["sector_dataset_complete"] = sector["dataset_complete"]

    raw = verify_raw_dataset_manifest(
        root / "data" / "backtest" / "raw_prices",
        root / "raw_dataset_manifest.json",
    )
    raw_match = bool(raw.get("raw_dataset_hash_match"))
    coverage = _daily_coverage_audit(root, raw.get("actual_raw_dataset_hash"))
    coverage_fresh = coverage["fresh"]
    result["raw_dataset_hash_match"] = raw_match
    result["daily_raw_coverage_fresh"] = coverage_fresh
    result["raw_dataset_integrity"] = raw
    result["daily_raw_coverage_binding"] = coverage["binding"]
    result["daily_raw_bar_coverage"] = coverage["min"]
    result["raw_bar_coverage_by_exchange"] = coverage["by_exchange"]
    result["daily_raw_coverage_audit"] = {
        "min_daily_raw_coverage": coverage["min"],
        "median_daily_raw_coverage": coverage["median"],
        "p05_daily_raw_coverage": coverage["p05"],
        "days_below_98pct": coverage["days_below_98pct"],
        "total_evaluated_days": coverage["days"],
    }

    raw_ready = bool(
        ca["complete"]
        and raw_match
        and coverage_fresh
        and coverage["min"] >= 0.98
        and coverage["days"] > 0
        and coverage["days_below_98pct"] == 0
        and all(value >= 0.98 for value in coverage["by_exchange"].values())
    )
    result["raw_execution_price_ready"] = raw_ready

    checklist = dict(result.get("criteria_checklist") or {})
    checklist.update(
        {
            "6_status_dataset_complete": status["dataset_complete"],
            "7_sector_dataset_complete": sector["dataset_complete"],
            "8_corporate_action_dataset_complete": ca["complete"],
            "9_raw_execution_price_ready": raw_ready,
            "10_daily_raw_bar_coverage": bool(
                coverage["min"] >= 0.98 and coverage["days"] > 0 and coverage["days_below_98pct"] == 0
            ),
            "11_each_exchange_raw_coverage": bool(
                coverage["by_exchange"] and all(value >= 0.98 for value in coverage["by_exchange"].values())
            ),
            "12_official_universe_set_match": universe_match,
            "16_raw_dataset_hash_match": raw_match,
            "17_daily_raw_coverage_fresh": coverage_fresh,
            "18_status_provenance_sidecar": bool(status["provenance_sidecar_present"]),
            "19_sector_provenance_sidecar": bool(sector["provenance_sidecar_present"]),
        }
    )
    result["criteria_checklist"] = checklist
    formal = bool(checklist and all(checklist.values()))
    result["formal_full_market_ready"] = formal
    result["research_grade_candidate"] = formal

    warnings = list(result.get("provider_warnings") or [])
    if not ca["official_register_valid"]:
        warnings.append("CORPORATE_ACTION_OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED")
    if not status["provenance_sidecar_present"]:
        warnings.append("STATUS_PROVENANCE_SIDECAR_MISSING")
    elif not status["dataset_complete"]:
        warnings.append("STATUS_PROVENANCE_INCOMPLETE")
    if not sector["provenance_sidecar_present"]:
        warnings.append("SECTOR_PROVENANCE_SIDECAR_MISSING")
    elif not sector["dataset_complete"]:
        warnings.append("SECTOR_PROVENANCE_INCOMPLETE")
    if not raw_match:
        warnings.append("RAW_DATASET_HASH_MISMATCH_OR_UNMOUNTED")
    if not coverage_fresh:
        warnings.append("DAILY_RAW_COVERAGE_STALE_OR_UNBOUND")
    result["provider_warnings"] = list(dict.fromkeys(warnings))
    return result


_legacy.run_research_preflight = run_research_preflight
