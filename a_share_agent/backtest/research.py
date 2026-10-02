"""Fail-closed research preflight for formal full-market A-share research.

The v0.7.4 research implementation is retained in ``research_legacy``. This wrapper
adds independent provenance, temporal coverage, security-master, benchmark-calendar,
trading-rule and dataset-integrity gates. No report/coverage artifact may define its
own expected trading calendar.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..git_utils import check_artifact_stale, get_git_metadata

from . import research_legacy as _legacy
from .data import HistoricalDataProvider
from .data_integrity import verify_coverage_binding, verify_raw_dataset_manifest
from .pit_interval_coverage import audit_pit_interval_coverage
from .preflight_checks import audit_benchmark_calendar, audit_trading_rules_runtime
from .provenance_audit import (
    audit_interval_provenance,
    is_a_share_common_equity_symbol,
    reconcile_corporate_action_sets,
)
from .raw_price_provenance import audit_raw_price_provenance
from .security_master_integrity import audit_security_master_integrity
from .service import settings_from
from .trusted_research_sources import audit_official_trading_calendar, audit_trading_rule_provenance
from .universe_reconciliation import reconcile_universe_snapshot_counts
from .universe_source_semantics import validate_universe_source_semantics

ResearchLab = _legacy.ResearchLab
research_validity = _legacy.research_validity
_metric_row = _legacy._metric_row
_neutral_sector_share = _legacy._neutral_sector_share
_ORIGINAL_PREFLIGHT = _legacy.run_research_preflight


def _is_a_share_common_equity_symbol(symbol: str, board: str) -> bool:
    return is_a_share_common_equity_symbol(symbol, board)


def _official_universe_reconciliation(config) -> dict[str, Any]:
    root = config.project_root
    return reconcile_universe_snapshot_counts(
        root / "data" / "backtest" / "security_master.csv",
        root / "data" / "backtest" / "official_universe_snapshots",
    )


def _official_universe_audit(config, mcp, overrides: dict[str, Any] | None) -> tuple[bool, int, int]:
    audit = _official_universe_reconciliation(config)
    return bool(audit["match"]), int(audit["extra_total"]), int(audit["missing_total"])


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
    dates: list[str] = []
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
                raw_value = str(row.get("active_raw_coverage") or row.get("raw_coverage_pct") or "").rstrip("%")
                try:
                    value = float(raw_value)
                    values.append(value / 100.0 if value > 1 else value)
                except ValueError:
                    continue
                trade_date = str(row.get("date") or row.get("trade_date") or "").strip()
                if trade_date:
                    dates.append(trade_date)
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
        "dates": sorted(set(dates)),
        "duplicate_date_rows": max(0, len(dates) - len(set(dates))),
        "by_exchange": {board: (min(board_values[board]) if board_values[board] else 0.0) for board in board_values},
    }


def run_research_preflight(config, mcp, *, overrides: dict[str, Any] | None = None, sample_size: int = 30) -> dict[str, Any]:
    result = _ORIGINAL_PREFLIGHT(config, mcp, overrides=overrides, sample_size=sample_size)
    root = config.project_root
    settings = settings_from(config, overrides or {})
    research_start = str(settings.start_date)
    research_end = str(settings.end_date)

    universe_audit = _official_universe_reconciliation(config)
    universe_set_match_integrity_bound = bool(universe_audit["match"])
    extra = int(universe_audit["extra_total"])
    missing = int(universe_audit["missing_total"])
    semantics = validate_universe_source_semantics(
        root / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
    )
    semantics_ready = bool(semantics["ready"])
    trusted_universe_match = bool(universe_set_match_integrity_bound and semantics_ready)
    result["official_universe_set_match_raw"] = bool(universe_audit.get("set_match_ignoring_integrity"))
    result["official_universe_snapshot_integrity_ready"] = bool(universe_audit.get("official_snapshot_integrity_ready"))
    result["official_universe_set_match"] = trusted_universe_match
    result["official_universe_source_semantics_verified"] = semantics_ready
    result["official_universe_source_semantics_audit"] = semantics
    result["universe_extra_symbol_count"] = extra
    result["universe_missing_symbol_count"] = missing
    result["universe_unique_missing_symbol_count"] = int(universe_audit.get("unique_missing_symbol_count", 0))
    result["universe_unique_extra_symbol_count"] = int(universe_audit.get("unique_extra_symbol_count", 0))
    result["universe_reconciliation"] = universe_audit
    detailed_diff = root / "official_universe_set_diff_detailed.csv"
    result["universe_detailed_diff_file"] = detailed_diff.name if detailed_diff.exists() else None

    ca = _corporate_action_audit(root)
    result["official_corporate_action_register_valid"] = ca["official_register_valid"]
    result["corporate_action_expected_vs_loaded"] = ca
    result["corporate_action_source_coverage"] = ca["ratio"]
    result["corporate_action_dataset_complete"] = ca["complete"]
    result["corporate_action_ready"] = ca["complete"]
    result["synthetic_corporate_actions_detected"] = ca["synthetic_events"]

    raw = verify_raw_dataset_manifest(
        root / "data" / "backtest" / "raw_prices",
        root / "raw_dataset_manifest.json",
    )
    raw_match = bool(raw.get("raw_dataset_hash_match"))
    raw_provenance = audit_raw_price_provenance(
        root,
        actual_dataset_hash=raw.get("actual_raw_dataset_hash"),
        research_start=research_start,
        research_end=research_end,
    )
    raw_provenance_verified = bool(raw_provenance.get("verified"))
    result["raw_price_provenance_audit"] = raw_provenance
    result["raw_price_provenance_verified"] = raw_provenance_verified

    coverage = _daily_coverage_audit(root, raw.get("actual_raw_dataset_hash"))
    coverage_fresh = coverage["fresh"]

    calendar_audit = audit_official_trading_calendar(
        root,
        research_start=research_start,
        research_end=research_end,
    )
    calendar_ready = bool(calendar_audit["verified"])
    trading_dates = list(calendar_audit["trading_dates"]) if calendar_ready else []
    coverage_window_dates = sorted(d for d in coverage["dates"] if research_start <= d <= research_end)
    calendar_set = set(trading_dates)
    coverage_set = set(coverage_window_dates)
    coverage_calendar_missing = sorted(calendar_set - coverage_set)
    coverage_calendar_extra = sorted(coverage_set - calendar_set)
    coverage_calendar_exact = bool(
        calendar_ready
        and coverage_fresh
        and not coverage_calendar_missing
        and not coverage_calendar_extra
        and not coverage["duplicate_date_rows"]
        and len(coverage_window_dates) == len(trading_dates)
    )

    result["trusted_trading_calendar_audit"] = calendar_audit
    result["trusted_trading_calendar_verified"] = calendar_ready
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
        "duplicate_date_rows": coverage["duplicate_date_rows"],
        "trusted_calendar_exact_match": coverage_calendar_exact,
        "missing_trading_dates": coverage_calendar_missing,
        "extra_coverage_dates": coverage_calendar_extra,
    }

    status_source = audit_interval_provenance(
        root / "data" / "backtest" / "historical_status_intervals.csv",
        root / "data" / "backtest" / "status_provenance.csv",
    )
    sector_source = audit_interval_provenance(
        root / "data" / "backtest" / "historical_sector_intervals.csv",
        root / "data" / "backtest" / "sector_provenance.csv",
    )
    status_pit = audit_pit_interval_coverage(
        root / "data" / "backtest" / "security_master.csv",
        root / "data" / "backtest" / "historical_status_intervals.csv",
        root / "data" / "backtest" / "status_provenance.csv",
        trading_dates,
        value_fields=("status",),
        source_audit=status_source,
    )
    sector_pit = audit_pit_interval_coverage(
        root / "data" / "backtest" / "security_master.csv",
        root / "data" / "backtest" / "historical_sector_intervals.csv",
        root / "data" / "backtest" / "sector_provenance.csv",
        trading_dates,
        value_fields=("sector_code", "industry_code", "sector", "industry"),
        source_audit=sector_source,
    )

    result["status_provenance_audit"] = status_source
    result["status_pit_coverage_audit"] = status_pit
    result["status_source_coverage"] = status_source["source_coverage"]
    result["status_dataset_complete"] = status_pit["dataset_complete"]
    result["sector_provenance_audit"] = sector_source
    result["sector_pit_coverage_audit"] = sector_pit
    result["sector_source_coverage"] = sector_source["source_coverage"]
    result["sector_dataset_complete"] = sector_pit["dataset_complete"]

    raw_ready = bool(
        raw_match
        and raw_provenance_verified
        and coverage_fresh
        and calendar_ready
        and coverage_calendar_exact
        and coverage["min"] >= 0.98
        and coverage["days"] > 0
        and coverage["days_below_98pct"] == 0
        and all(value >= 0.98 for value in coverage["by_exchange"].values())
    )
    result["raw_execution_price_ready"] = raw_ready

    master_integrity = audit_security_master_integrity(
        root / "data" / "backtest" / "security_master.csv",
        root / "data" / "backtest" / "official_universe_snapshots",
        research_start=research_start,
        research_end=research_end,
        trading_dates=trading_dates,
    )
    result["security_master_integrity_audit"] = master_integrity
    no_prelisting_leakage = not bool(master_integrity["prelisting_leakage_symbols"])
    no_post_delisting_leakage = not bool(master_integrity["post_delisting_leakage_symbols"])
    no_survivorship_bias = bool(trusted_universe_match and master_integrity["survivorship_bias_protection_ready"])
    result["survivorship_bias"] = not no_survivorship_bias
    result["no_survivorship_bias"] = no_survivorship_bias
    if isinstance(result.get("universe"), dict):
        result["universe"]["survivorship_bias"] = not no_survivorship_bias
        result["universe"]["survivorship_bias_protection_ready"] = no_survivorship_bias

    benchmark_provider = HistoricalDataProvider(root, mcp, use_cache=settings.cache)
    benchmark_bars = benchmark_provider.bars(
        settings.benchmark,
        count=max(1200, int(settings.warmup_bars) + len(trading_dates) + 100),
    )
    benchmark_audit = audit_benchmark_calendar(
        [str(row.get("date") or "") for row in benchmark_bars],
        trading_dates,
        research_start=research_start,
        research_end=research_end,
        warmup_required=int(settings.warmup_bars),
    )
    result["benchmark_strict_coverage_audit"] = benchmark_audit
    if isinstance(result.get("benchmark"), dict):
        result["benchmark"]["strict_window_complete"] = benchmark_audit["complete"]
        result["benchmark"]["missing_trading_dates"] = benchmark_audit["missing_trading_dates"]
        result["benchmark"]["warmup_bars"] = benchmark_audit["benchmark_warmup_bars"]

    trading_rules_runtime = audit_trading_rules_runtime()
    trading_rule_provenance = audit_trading_rule_provenance(
        root,
        list((trading_rules_runtime.get("checks") or {}).keys()),
    )
    trading_rules_research_verified = bool(
        trading_rules_runtime["verified"] and trading_rule_provenance["verified"]
    )
    result["historical_trading_rules_audit"] = trading_rules_runtime
    result["historical_trading_rule_provenance_audit"] = trading_rule_provenance
    # Backward-compatible diagnostic: runtime implementation matches its configured rules.
    # Formal research readiness uses the separately provenance-bound field/checklist below.
    result["historical_trading_rules_verified"] = bool(trading_rules_runtime["verified"])
    result["historical_trading_rules_research_verified"] = trading_rules_research_verified

    min_symbols = int((config.research or {}).get("min_symbols_for_research_grade", 5000))
    daily_active_universe_ok = bool(
        calendar_ready
        and trading_dates
        and master_integrity["min_daily_active_symbols"] >= min_symbols
    )
    result["daily_active_universe_audit"] = {
        "trading_date_count": master_integrity["trading_date_count"],
        "min_daily_active_symbols": master_integrity["min_daily_active_symbols"],
        "max_daily_active_symbols": master_integrity["max_daily_active_symbols"],
        "min_symbols_required": min_symbols,
        "trusted_calendar_verified": calendar_ready,
        "complete": daily_active_universe_ok,
    }

    checklist = dict(result.get("criteria_checklist") or {})
    checklist.update(
        {
            "1_market_universe_coverage": daily_active_universe_ok,
            "3_historical_delisted_preserved": bool(master_integrity["delisted_register_complete"]),
            "4_no_prelisting_leakage": no_prelisting_leakage,
            "5_no_post_delisting_leakage": no_post_delisting_leakage,
            "6_status_dataset_complete": status_pit["dataset_complete"],
            "7_sector_dataset_complete": sector_pit["dataset_complete"],
            "8_corporate_action_dataset_complete": ca["complete"],
            "9_raw_execution_price_ready": raw_ready,
            "10_daily_raw_bar_coverage": bool(
                coverage_calendar_exact
                and coverage["min"] >= 0.98
                and coverage["days"] > 0
                and coverage["days_below_98pct"] == 0
            ),
            "11_each_exchange_raw_coverage": bool(
                coverage_calendar_exact
                and coverage["by_exchange"]
                and all(value >= 0.98 for value in coverage["by_exchange"].values())
            ),
            "12_official_universe_set_match": trusted_universe_match,
            "13_historical_trading_rules_verified": trading_rules_research_verified,
            "14_benchmark_coverage": bool(calendar_ready and benchmark_audit["complete"]),
            "15_survivorship_bias": no_survivorship_bias,
            "16_raw_dataset_hash_match": raw_match,
            "17_daily_raw_coverage_fresh": coverage_fresh,
            "18_status_provenance_sidecar": bool(status_source["provenance_sidecar_present"]),
            "19_sector_provenance_sidecar": bool(sector_source["provenance_sidecar_present"]),
            "20_official_universe_source_semantics_verified": semantics_ready,
            "21_status_full_window_pit_coverage": bool(calendar_ready and status_pit["dataset_complete"]),
            "22_sector_full_window_pit_coverage": bool(calendar_ready and sector_pit["dataset_complete"]),
            "23_security_master_interval_integrity": bool(master_integrity["boundary_integrity"]),
            "24_delisted_register_reconciliation_complete": bool(master_integrity["delisted_register_complete"]),
            "25_benchmark_exact_calendar_and_warmup": bool(calendar_ready and benchmark_audit["complete"]),
            "26_trusted_trading_calendar_verified": calendar_ready,
            "27_daily_raw_coverage_exact_calendar": coverage_calendar_exact,
            "28_official_snapshot_and_register_hashes_verified": bool(universe_audit.get("official_snapshot_integrity_ready")),
            "29_trading_rule_provenance_verified": bool(trading_rule_provenance["verified"]),
            "30_raw_price_provenance_verified": raw_provenance_verified,
        }
    )

    git_meta = get_git_metadata(root)
    current_head = git_meta.get("git_commit_sha")
    commit_bound_source = bool(git_meta.get("commit_bound_execution_ready"))
    checklist["31_commit_bound_clean_source"] = commit_bound_source
    result["criteria_checklist"] = checklist
    result["git_commit_sha"] = current_head
    result["git_branch"] = git_meta.get("git_branch")
    result["working_tree_clean"] = git_meta.get("working_tree_clean")
    result["tracked_tree_clean"] = git_meta.get("tracked_tree_clean")
    result["commit_bound_execution_ready"] = commit_bound_source
    result["source_provenance_reason"] = git_meta.get("source_provenance_reason")
    result["generated_at"] = git_meta.get("generated_at")
    result["research_start"] = research_start
    result["research_end"] = research_end
    result["producer_git_commit"] = current_head
    result["producer_code_version"] = git_meta.get("producer_code_version")
    result["preflight_execution_ok"] = bool(result.get("ok", True))

    def _read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    stale_artifacts: list[str] = []
    manifest_raw = _read_json_file(root / "raw_dataset_manifest.json")
    if (root / "raw_dataset_manifest.json").exists() and check_artifact_stale(manifest_raw, current_head, repo_root=root):
        stale_artifacts.append("raw_dataset_manifest.json")

    manifest_cov = _read_json_file(root / "daily_raw_coverage_manifest.json")
    if (root / "daily_raw_coverage_manifest.json").exists() and check_artifact_stale(manifest_cov, current_head, repo_root=root):
        stale_artifacts.append("daily_raw_coverage_manifest.json")

    audit_prov = _read_json_file(root / "historical_data_provenance_audit.json")
    if (root / "historical_data_provenance_audit.json").exists() and check_artifact_stale(audit_prov, current_head, repo_root=root):
        stale_artifacts.append("historical_data_provenance_audit.json")

    summary_miss = _read_json_file(root / "security_master_missing_history_readiness_summary.json")
    if (root / "security_master_missing_history_readiness_summary.json").exists() and check_artifact_stale(summary_miss, current_head, repo_root=root):
        stale_artifacts.append("security_master_missing_history_readiness_summary.json")

    any_stale = bool(stale_artifacts)
    preflight_current = bool(current_head)

    result["preflight_artifact_current"] = preflight_current
    result["artifact_stale"] = any_stale
    result["stale_readiness_artifacts"] = stale_artifacts

    formal = bool(checklist and all(checklist.values()) and not any_stale and preflight_current)
    result["formal_full_market_ready"] = formal
    result["research_grade_candidate"] = formal

    warnings = list(result.get("provider_warnings") or [])
    if not universe_audit.get("official_snapshot_integrity_ready"):
        warnings.append("OFFICIAL_UNIVERSE_SNAPSHOT_OR_REGISTER_HASH_INTEGRITY_FAILED")
    if not semantics_ready:
        warnings.append("OFFICIAL_UNIVERSE_SOURCE_DATE_SEMANTICS_UNVERIFIED")
    if not ca["official_register_valid"]:
        warnings.append("CORPORATE_ACTION_OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED")
    if not calendar_ready:
        warnings.append("TRUSTED_TRADING_CALENDAR_UNAVAILABLE_OR_UNVERIFIED")
    if not coverage_calendar_exact:
        warnings.append("DAILY_RAW_COVERAGE_TRADING_CALENDAR_MISMATCH")
    if not status_source["provenance_sidecar_present"]:
        warnings.append("STATUS_PROVENANCE_SIDECAR_MISSING")
    elif not status_source["dataset_complete"]:
        warnings.append("STATUS_PROVENANCE_INCOMPLETE")
    elif not status_pit["dataset_complete"]:
        warnings.append("STATUS_PIT_TEMPORAL_COVERAGE_INCOMPLETE")
    if not sector_source["provenance_sidecar_present"]:
        warnings.append("SECTOR_PROVENANCE_SIDECAR_MISSING")
    elif not sector_source["dataset_complete"]:
        warnings.append("SECTOR_PROVENANCE_INCOMPLETE")
    elif not sector_pit["dataset_complete"]:
        warnings.append("SECTOR_PIT_TEMPORAL_COVERAGE_INCOMPLETE")
    if not raw_match:
        warnings.append("RAW_DATASET_HASH_MISMATCH_OR_UNMOUNTED")
    if not raw_provenance_verified:
        warnings.append("RAW_PRICE_PROVENANCE_UNAVAILABLE_OR_UNVERIFIED")
    if not coverage_fresh:
        warnings.append("DAILY_RAW_COVERAGE_STALE_OR_UNBOUND")
    if not master_integrity["boundary_integrity"]:
        warnings.append("SECURITY_MASTER_INTERVAL_BOUNDARY_INTEGRITY_FAILED")
    if not master_integrity["delisted_register_complete"]:
        warnings.append("HISTORICAL_DELISTED_REGISTER_RECONCILIATION_INCOMPLETE")
    if not benchmark_audit["complete"]:
        warnings.append("BENCHMARK_CALENDAR_OR_WARMUP_INCOMPLETE")
    if not trading_rules_runtime["verified"]:
        warnings.append("HISTORICAL_TRADING_RULE_RUNTIME_CHECK_FAILED")
    if not trading_rule_provenance["verified"]:
        warnings.append("HISTORICAL_TRADING_RULE_PROVENANCE_INCOMPLETE")
    if not commit_bound_source:
        warnings.append("SOURCE_TREE_NOT_CLEAN_OR_COMMIT_BOUND")
    if any_stale:
        warnings.append(f"STALE_READINESS_ARTIFACTS_DETECTED: {','.join(stale_artifacts)}")
    if not preflight_current:
        warnings.append("PREFLIGHT_ARTIFACT_NOT_CURRENT_WITH_HEAD")
    result["provider_warnings"] = list(dict.fromkeys(warnings))
    return result


_legacy.run_research_preflight = run_research_preflight
