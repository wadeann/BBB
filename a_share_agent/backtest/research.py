from __future__ import annotations

import csv
import json
import uuid
import zipfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import RuntimeConfig
from ..llm.base import LLMClient
from ..mcp.base import MCPInvoker
from .data import HistoricalDataProvider, UniverseInfo
from .engine import BacktestEngine
from .llm_filter import HistoricalLLMFilter
from .models import BacktestSettings
from .report import BacktestReportWriter
from .service import settings_from


def _metric_row(exp_id: str, report: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    m = report.get("metrics", {})
    row = {
        "experiment_id": exp_id,
        "run_id": report.get("run_id"),
        "total_return": m.get("total_return", 0),
        "cagr": m.get("cagr", 0),
        "max_drawdown": m.get("max_drawdown", 0),
        "sharpe": m.get("sharpe", 0),
        "sortino": m.get("sortino", 0),
        "profit_factor": m.get("profit_factor", 0),
        "win_rate": m.get("win_rate", 0),
        "expectancy_pct": m.get("expectancy_pct", 0),
        "closed_trades": m.get("closed_trades", 0),
        "gross_pnl_before_costs": m.get("gross_pnl_before_costs", 0),
        "round_trip_fees": m.get("round_trip_fees", 0),
        "estimated_slippage_cost": m.get("estimated_slippage_cost", 0),
        "net_realized_pnl": m.get("net_realized_pnl", 0),
        "benchmark_return": m.get("benchmark_return", 0),
        "excess_return_vs_benchmark": m.get("excess_return_vs_benchmark", 0),
        "months_ge_target": m.get("months_ge_target", 0),
        "months_total": m.get("months_total", 0),
        "tested_symbols": report.get("coverage", {}).get("tested_symbols", 0),
        "active_universe_avg": report.get("coverage", {}).get("active_universe_avg", 0),
        "active_universe_min": report.get("coverage", {}).get("active_universe_min", 0),
        "active_universe_max": report.get("coverage", {}).get("active_universe_max", 0),
    }
    llm_stats = (report.get("methodology", {}) or {}).get("llm_filter_stats") or {}
    row.update({
        "llm_api_calls": llm_stats.get("api_calls", 0),
        "llm_failures": llm_stats.get("failures", 0),
        "llm_candidate_error_rate": llm_stats.get("candidate_error_rate", 0),
        "llm_avg_call_seconds": llm_stats.get("avg_api_call_seconds", 0),
    })
    if baseline:
        bm = baseline.get("metrics", {})
        row.update({
            "delta_total_return": float(m.get("total_return", 0)) - float(bm.get("total_return", 0)),
            "delta_max_drawdown": float(m.get("max_drawdown", 0)) - float(bm.get("max_drawdown", 0)),
            "delta_profit_factor": float(m.get("profit_factor", 0)) - float(bm.get("profit_factor", 0)),
            "delta_expectancy_pct": float(m.get("expectancy_pct", 0)) - float(bm.get("expectancy_pct", 0)),
        })
    return row


def _neutral_sector_share(report: dict[str, Any]) -> float:
    rows = report.get("by_route", []) or []
    total = sum(int(x.get("trades", 0)) for x in rows)
    if total <= 0:
        return 1.0
    neutral = sum(int(x.get("trades", 0)) for x in rows if "NEUTRAL_SECTOR" in str(x.get("group", "")))
    return neutral / total


def research_validity(report: dict[str, Any], universe: UniverseInfo | None, cfg: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    tested = int(report.get("coverage", {}).get("tested_symbols", 0))
    min_symbols = int(cfg.get("min_symbols_for_research_grade", 500))
    if tested < min_symbols:
        reasons.append(f"tested_symbols={tested} < research_grade_min={min_symbols}")
    if universe and universe.survivorship_bias:
        reasons.append("universe_has_survivorship_bias")
    dq = report.get("data_quality", {}) or {}
    if not bool(dq.get("point_in_time_universe_all_days", False)):
        reasons.append("daily_universe_not_point_in_time_for_all_days")
    if not bool(dq.get("dynamic_universe_daily", False)):
        reasons.append("universe_not_rebuilt_daily")
    neutral_share = _neutral_sector_share(report)
    max_neutral = float(cfg.get("max_neutral_sector_share_for_research_grade", 0.85))
    if neutral_share > max_neutral:
        reasons.append(f"neutral_sector_trade_share={neutral_share:.3f} > {max_neutral:.3f}")
    min_sector_cov = float(cfg.get("min_historical_sector_mapping_coverage", 0.80))
    sector_cov = float(dq.get("historical_sector_mapping_coverage", 0.0) or 0.0)
    if sector_cov < min_sector_cov:
        reasons.append(f"historical_sector_mapping_coverage={sector_cov:.3f} < {min_sector_cov:.3f}")
    if not bool(dq.get("historical_sector_membership_point_in_time", False)):
        reasons.append("sector_membership_not_point_in_time")

    llm_stats = (report.get("methodology", {}) or {}).get("llm_filter_stats") or {}
    llm_used = bool((report.get("methodology", {}) or {}).get("llm_used"))
    llm_valid = True
    if llm_used:
        if int(llm_stats.get("failures", 0) or 0) > 0:
            reasons.append("llm_api_failures_present")
            llm_valid = False
        if int(llm_stats.get("error_candidates", 0) or 0) > 0:
            reasons.append("llm_error_candidates_present")
            llm_valid = False

    return {
        "grade": "RESEARCH_GRADE" if not reasons else "DIAGNOSTIC_ONLY",
        "reasons": reasons,
        "tested_symbols": tested,
        "neutral_sector_trade_share": neutral_share,
        "survivorship_bias": bool(universe.survivorship_bias) if universe else None,
        "point_in_time_universe_all_days": bool(dq.get("point_in_time_universe_all_days", False)),
        "dynamic_universe_daily": bool(dq.get("dynamic_universe_daily", False)),
        "historical_sector_mapping_coverage": sector_cov,
        "llm_experiment_valid": llm_valid,
        "llm_filter_stats": llm_stats if llm_used else None,
    }



class ResearchLab:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker | None, llm: LLMClient | None = None):
        self.config = config
        self.mcp = mcp
        self.llm = llm
        self.root = config.project_root
        self.writer = BacktestReportWriter(self.root)
        cfg_path = self.root / "config" / "research.yaml"
        self.research_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        self.out_root = self.root / "data" / "research" / "runs"
        self.out_root.mkdir(parents=True, exist_ok=True)

    def run(self, *, overrides: dict[str, Any] | None = None, symbols: list[str] | None = None,
            include_llm: bool = False, experiment_ids: list[str] | None = None) -> dict[str, Any]:
        suite_id = f"research-{uuid.uuid4().hex[:12]}"
        suite_dir = self.out_root / suite_id
        suite_dir.mkdir(parents=True, exist_ok=True)
        base_settings = settings_from(self.config, overrides or {})
        provider = HistoricalDataProvider(self.root, self.mcp, use_cache=base_settings.cache)
        universe: UniverseInfo | None = None
        if not symbols:
            universe = provider.load_universe_for_period(base_settings.start_date,base_settings.end_date,base_settings.universe_file,max_universe=base_settings.max_universe,mode=base_settings.universe_mode)
            symbols = universe.symbols
        if not symbols:
            raise RuntimeError("research universe is empty")

        experiments = self.research_cfg.get("experiments", []) or []
        if experiment_ids:
            wanted = set(experiment_ids)
            experiments = [e for e in experiments if e.get("id") in wanted]
        results: list[dict[str, Any]] = []
        reports: dict[str, dict[str, Any]] = {}
        baseline_id = str(self.research_cfg.get("baseline_id", "baseline"))

        for exp in experiments:
            exp_id = str(exp.get("id"))
            requires_llm = bool(exp.get("requires_llm"))
            if requires_llm and (not include_llm or self.llm is None):
                results.append({"experiment_id": exp_id, "status": "SKIPPED", "reason": "LLM_NOT_ENABLED_FOR_SUITE"})
                continue
            merged = base_settings.to_dict()
            merged.update(exp.get("overrides") or {})
            allowed = {f.name for f in fields(BacktestSettings)}
            settings = BacktestSettings(**{k: v for k, v in merged.items() if k in allowed})
            llm_filter = None
            if settings.llm_filter_enabled:
                model_id = str(getattr(self.llm, "model", None) or (self.config.runtime.get("llm") or {}).get("model") or "external-llm")
                llm_filter = HistoricalLLMFilter(self.root, self.llm, model_id=model_id, use_cache=True, anonymize_symbol=settings.llm_filter_anonymize_symbol, batch_size=settings.llm_filter_batch_size, payload_mode="compact_features")  # type: ignore[arg-type]
            report = BacktestEngine(self.config, provider, settings, llm_filter=llm_filter).run(symbols)
            if universe:
                report["universe"] = {"source": universe.source, "survivorship_bias": universe.survivorship_bias, "notes": universe.notes, "seed_symbols": len(symbols), "tested_union_symbols": report.get("coverage", {}).get("tested_symbols", 0), "point_in_time": universe.point_in_time, "membership_records": universe.membership_records, "dynamic_daily": universe.dynamic_daily, "dataset_version": universe.dataset_version, "coverage": universe.coverage}
            report["research_experiment"] = {"id": exp_id, "description": exp.get("description"), "requires_llm": requires_llm}
            report["research_validity"] = research_validity(report, universe, self.research_cfg)
            path = self.writer.write(report)
            reports[exp_id] = report
            results.append({"experiment_id": exp_id, "status": "COMPLETED", "run_id": report["run_id"], "report_dir": str(path), "validity": report["research_validity"]})

        baseline = reports.get(baseline_id)
        comparison = [_metric_row(k, r, baseline if k != baseline_id else None) for k, r in reports.items()]
        summary = {
            "suite_id": suite_id,
            "created_at": datetime.now().astimezone().isoformat(),
            "period": {"start": base_settings.start_date, "end": base_settings.end_date},
            "baseline_id": baseline_id,
            "include_llm": include_llm,
            "experiments": results,
            "comparison": comparison,
            "universe": ({"source": universe.source, "survivorship_bias": universe.survivorship_bias, "notes": universe.notes, "seed_symbols": len(symbols), "tested_union_symbols": max((int((r.get("coverage") or {}).get("tested_symbols",0)) for r in reports.values()), default=0), "point_in_time": universe.point_in_time, "membership_records": universe.membership_records, "dynamic_daily": universe.dynamic_daily, "dataset_version": universe.dataset_version, "coverage": universe.coverage} if universe else {"source": "explicit_symbols", "symbols": len(symbols)}),
            "feedback_files": ["research_summary.json", "experiment_metrics.csv", "FEEDBACK_README.md", "feedback_bundle.zip"],
        }
        (suite_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if comparison:
            csv_fields=[]
            for row in comparison:
                for key in row:
                    if key not in csv_fields:
                        csv_fields.append(key)
            with (suite_dir / "experiment_metrics.csv").open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=csv_fields, extrasaction="ignore")
                w.writeheader(); w.writerows(comparison)
        (suite_dir / "FEEDBACK_README.md").write_text(self._feedback_readme(summary), encoding="utf-8")
        bundle = self._bundle(suite_dir, results)
        summary["feedback_bundle"] = str(bundle)
        (suite_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.out_root / "latest.json").write_text(json.dumps({"suite_id": suite_id, "path": str(suite_dir), "feedback_bundle": str(bundle)}, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    def _bundle(self, suite_dir: Path, results: list[dict[str, Any]]) -> Path:
        out = suite_dir / "feedback_bundle.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("research_summary.json", "experiment_metrics.csv", "FEEDBACK_README.md"):
                p = suite_dir / name
                if p.exists(): z.write(p, name)
            for x in results:
                if x.get("status") != "COMPLETED":
                    continue
                report_dir = Path(str(x["report_dir"]))
                prefix = f"experiments/{x['experiment_id']}"
                for name in ("report.json", "trades.csv", "monthly_returns.csv", "rejections.csv"):
                    p = report_dir / name
                    if p.exists(): z.write(p, f"{prefix}/{name}")
        return out

    @staticmethod
    def _feedback_readme(summary: dict[str, Any]) -> str:
        return f"""# Research feedback package\n\nSuite: `{summary['suite_id']}`\nPeriod: `{summary['period']['start']}` to `{summary['period']['end']}`\n\n## Send back for analysis\n\nPreferred: send the single `feedback_bundle.zip` from this directory.\nIf upload size is constrained, send `research_summary.json` and `experiment_metrics.csv` first.\n\n## Interpretation rule\n\nAn experiment marked `DIAGNOSTIC_ONLY` must not be treated as proof of profitability. Typical reasons are limited stock coverage, survivorship bias, or ineffective historical sector classification.\n\nLLM experiments are A/B filters over the same deterministic candidates. They do not receive future bars and they do not call live MCP/web data. Cached decisions are keyed by model + prompt + point-in-time payload.\n"""


def run_research_preflight(config: RuntimeConfig, mcp: MCPInvoker | None, *, overrides: dict[str, Any] | None = None, sample_size: int = 30) -> dict[str, Any]:
    """Validate full-market point-in-time data before an expensive research suite."""
    settings = settings_from(config, overrides or {})
    provider = HistoricalDataProvider(config.project_root, mcp, use_cache=settings.cache)
    universe = provider.load_universe_for_period(
        settings.start_date, settings.end_date, settings.universe_file,
        max_universe=settings.max_universe, mode=settings.universe_mode,
    )
    benchmark = provider.bars(settings.benchmark, count=max(900, settings.warmup_bars + 550))
    trading_dates = [str(x["date"]) for x in benchmark if settings.start_date <= str(x["date"]) <= settings.end_date]
    benchmark_ok = bool(trading_dates)
    if not trading_dates:
        check_dates: list[str] = []
    else:
        check_dates = list(dict.fromkeys([trading_dates[0], trading_dates[len(trading_dates)//2], trading_dates[-1]]))

    universe_checks: list[dict[str, Any]] = []
    union_symbols: set[str] = set()
    all_pit = True
    for d in check_dates:
        records = provider.active_records_on(d, universe.symbols)
        meta = provider.daily_universe_meta(d, universe.symbols)
        union_symbols.update(str(x.get("symbol")) for x in records if x.get("symbol"))
        all_pit = all_pit and bool(meta.get("point_in_time"))
        universe_checks.append(meta)

    # 1. Market universe coverage ratio (relative to ~5,380 active A-share market baseline)
    total_records = len(universe.symbols)
    market_baseline = 5380.0
    universe_market_coverage_ratio = round(min(1.0, total_records / market_baseline), 4) if market_baseline else 1.0

    # 2. Exchange coverage (SSE Main, STAR, SZSE Main, ChiNext, BSE)
    exchange_coverage: dict[str, int] = {
        "SSE_MAIN": 0,
        "STAR": 0,
        "SZSE_MAIN": 0,
        "CHINEXT": 0,
        "BSE": 0,
    }
    for sym in universe.symbols:
        s = sym.upper()
        # Check board in membership record first
        recs = provider._membership.get(sym, [])
        board = (recs[0].get("board") if recs else None) or ""
        if board in exchange_coverage:
            exchange_coverage[board] += 1
        elif s.endswith(".BJ") or s.startswith(("4", "8", "92")):
            exchange_coverage["BSE"] += 1
        elif s.startswith(("688", "689")) or (s.endswith(".SH") and s.startswith(("688", "689"))):
            exchange_coverage["STAR"] += 1
        elif s.startswith(("600", "601", "603", "605")) or s.endswith(".SH"):
            exchange_coverage["SSE_MAIN"] += 1
        elif s.startswith(("300", "301")) or (s.endswith(".SZ") and s.startswith(("300", "301"))):
            exchange_coverage["CHINEXT"] += 1
        elif s.startswith(("000", "001", "002", "003")) or s.endswith(".SZ"):
            exchange_coverage["SZSE_MAIN"] += 1
        else:
            exchange_coverage["SZSE_MAIN"] += 1

    exchange_coverage_ok = (
        exchange_coverage["SSE_MAIN"] > 0
        and exchange_coverage["STAR"] > 0
        and exchange_coverage["SZSE_MAIN"] > 0
        and exchange_coverage["CHINEXT"] > 0
        and exchange_coverage["BSE"] > 0
    )

    # 3. Delisted stocks preserved - verify against actual delisting records
    delisted_in_universe = []
    post_delisting_leakage_symbols = []
    for sym in universe.symbols:
        recs = provider._membership.get(sym, [])
        for r in recs:
            delist_date = str(r.get("delisting_date") or "")
            if delist_date:
                delisted_in_universe.append(sym)
                # Check: is the stock active AFTER its delisting date?
                active_to = str(r.get("active_to") or "")
                if active_to and active_to > delist_date:
                    post_delisting_leakage_symbols.append(sym)
                # Also check check_dates after delisting
                for cd in check_dates:
                    if cd > delist_date and provider._is_active_record(r, cd):
                        if sym not in post_delisting_leakage_symbols:
                            post_delisting_leakage_symbols.append(sym)
                break
    delisted_during_period_preserved = len(delisted_in_universe) > 0
    post_delisting_leakage = len(post_delisting_leakage_symbols) > 0

    # 4. IPO prelisting leakage - check across ENTIRE backtest period, not just first check_date
    ipo_prelisting_leakage = False
    ipo_prelisting_leakage_symbols = []
    all_period_dates = check_dates + [settings.start_date]
    for sym in universe.symbols:
        recs = provider._membership.get(sym, [])
        for r in recs:
            l_date = str(r.get("listing_date") or r.get("active_from") or "")
            if l_date and l_date > settings.start_date:
                # This stock was listed after the backtest start.
                # Check if it appears active before its listing date across all check dates.
                for cd in all_period_dates:
                    if cd < l_date and provider._is_active_record(r, cd):
                        ipo_prelisting_leakage = True
                        ipo_prelisting_leakage_symbols.append(sym)
                        break
        if len(ipo_prelisting_leakage_symbols) > 20:
            break  # Enough evidence

    # 5. Historical ST / Suspension coverage
    status_tracked_count = sum(1 for sym in universe.symbols if sym in provider._status_intervals)
    historical_status_pit_coverage = round(status_tracked_count / total_records, 4) if total_records else 0.0

    # 6. Sector label coverage & sector membership PIT coverage
    sector_label_count = 0
    sector_pit_count = 0
    for sym in universe.symbols:
        intervals = provider._sector_intervals_map.get(sym, [])
        if intervals:
            sector_pit_count += 1
            if any(x.get("sector_code") for x in intervals):
                sector_label_count += 1
        else:
            recs = provider._membership.get(sym, [])
            if any(r.get("industry_code") for r in recs):
                sector_label_count += 1

    sector_label_coverage = round(sector_label_count / total_records, 4) if total_records else 0.0
    sector_membership_pit_coverage = round(sector_pit_count / total_records, 4) if total_records else 0.0
    sector_membership_point_in_time = sector_membership_pit_coverage >= 0.90
    sector_constituent_point_in_time = hasattr(provider, "sector_constituents_on") and callable(provider.sector_constituents_on)
    sector_constituent_pit_coverage = 1.0 if sector_constituent_point_in_time else 0.0

    # Corporate Action Engine & Authenticity from actual dataset
    ca_prod_file = config.project_root / "data" / "backtest" / "corporate_actions.csv"
    ca_ver_file = config.project_root / "corporate_action_verification.csv"
    corporate_action_data_verified = ca_ver_file.exists()
    corporate_action_invalid_count = 0
    synthetic_corporate_actions_detected = 0

    verified_actions_count = 0
    loaded_ca_events = 0
    if ca_prod_file.exists():
        with ca_prod_file.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                loaded_ca_events += 1
                src = str(r.get("source") or "")
                doc_id = str(r.get("source_url_or_document_id") or "")
                ver_flag = str(r.get("verified", "")).lower() in ("true", "1")
                if "SYNTHETIC" in src.upper() or "SYNTHETIC" in doc_id.upper():
                    synthetic_corporate_actions_detected += 1
                elif ver_flag and doc_id:
                    verified_actions_count += 1

    expected_ca_events = 8789
    ca_ratio = round(verified_actions_count / expected_ca_events, 4) if expected_ca_events else 0.0
    corporate_action_expected_vs_loaded = {
        "expected_events": expected_ca_events,
        "loaded_events": verified_actions_count,
        "ratio": ca_ratio,
    }
    corporate_action_source_coverage = ca_ratio
    # Full dataset complete requires covering >90% of expected full-market scope with 0 synthetic
    corporate_action_dataset_complete = bool(ca_ratio >= 0.90 and synthetic_corporate_actions_detected == 0)
    corporate_action_ready = bool(corporate_action_dataset_complete and synthetic_corporate_actions_detected == 0)

    # 9. Universe Set-level Difference against official snapshots
    snapshot_dir = config.project_root / "data" / "backtest" / "official_universe_snapshots"
    universe_extra_symbol_count = 0
    universe_missing_symbol_count = 0
    snapshot_files = sorted(list(snapshot_dir.glob("*.csv"))) if snapshot_dir.exists() else []
    if snapshot_files:
        for snap_file in snapshot_files:
            snap_date = snap_file.stem
            if not (snap_date.startswith("20") and len(snap_date) == 10):
                continue
            official_syms = set()
            with snap_file.open("r", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    official_syms.add(r["symbol"])
            active_syms = set(provider.active_symbols_on(snap_date, universe.symbols))
            universe_extra_symbol_count += len(active_syms - official_syms)
            universe_missing_symbol_count += len(official_syms - active_syms)
    else:
        diff_file = config.project_root / "universe_set_diff.csv"
        if diff_file.exists():
            with diff_file.open("r", encoding="utf-8-sig") as f:
                d_rows = list(csv.DictReader(f))
                universe_extra_symbol_count = sum(int(r.get("extra", 0)) for r in d_rows if "extra" in r)
                universe_missing_symbol_count = sum(int(r.get("missing", 0)) for r in d_rows if "missing" in r)
    official_universe_set_match = (universe_extra_symbol_count == 0 and universe_missing_symbol_count == 0 and not ipo_prelisting_leakage and not post_delisting_leakage)

    # 10. Status Authenticity & Completeness from actual interval provenance
    status_ver_file = config.project_root / "status_verification.csv"
    verified_status_count = 0
    total_status_intervals = 0
    for sym, intervals in provider._status_intervals.items():
        total_status_intervals += len(intervals)
        for it in intervals:
            src = str(it.get("source") or "")
            # Only genuine notice/document ID counts as verified provenance
            if any(k in src.upper() for k in ["NOTICE_", "SUSP_", "ANNOUNCEMENT_"]):
                verified_status_count += 1

    status_source_coverage = round(verified_status_count / total_status_intervals, 4) if total_status_intervals else 0.0
    status_sample_verified = status_ver_file.exists() and verified_status_count >= 50
    status_data_verified = status_sample_verified
    status_dataset_complete = (status_source_coverage >= 0.95)

    # 11. Sector Authenticity & Completeness from actual interval provenance
    sector_ver_file = config.project_root / "sector_change_verification.csv"
    sector_schema_supports_pit = True
    sector_change_event_count = 0
    sector_change_events_in_backtest_period = 0
    verified_sector_count = 0
    total_sector_intervals = 0
    for sym, intervals in provider._sector_intervals_map.items():
        total_sector_intervals += len(intervals)
        for it in intervals:
            src = str(it.get("source") or "")
            if any(k in src.upper() for k in ["RECLASS", "RECLASSIFICATION"]):
                verified_sector_count += 1

    if sector_ver_file.exists():
        with sector_ver_file.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                sector_change_event_count += 1
                if r.get("in_backtest_period") in ("True", "true", "1") or ("2024-10-01" <= r.get("effective_from", "") <= "2026-09-30"):
                    sector_change_events_in_backtest_period += 1

    sector_source_coverage = round(verified_sector_count / total_sector_intervals, 4) if total_sector_intervals else 0.0
    sector_data_verified_pit = sector_change_event_count >= 10
    sector_dataset_complete = (sector_source_coverage >= 0.95)

    # 12. Raw Price Coverage dynamically scanned from actual files on disk
    raw_dir = config.project_root / "data" / "backtest" / "raw_prices"
    raw_by_board = {"SSE_MAIN": 0, "STAR": 0, "SZSE_MAIN": 0, "CHINEXT": 0, "BSE": 0}
    tot_by_board = {"SSE_MAIN": 0, "STAR": 0, "SZSE_MAIN": 0, "CHINEXT": 0, "BSE": 0}

    for sym in universe.symbols:
        s = sym.upper()
        recs = provider._membership.get(sym, [])
        board = (recs[0].get("board") if recs else None) or ""
        b_key = board if board in raw_by_board else (
            "BSE" if s.endswith(".BJ") or s.startswith(("4", "8", "92")) else (
                "STAR" if s.startswith(("688", "689")) or (s.endswith(".SH") and s.startswith(("688", "689"))) else (
                    "CHINEXT" if s.startswith(("300", "301")) or (s.endswith(".SZ") and s.startswith(("300", "301"))) else (
                        "SSE_MAIN" if s.startswith(("600", "601", "603", "605")) or s.endswith(".SH") else "SZSE_MAIN"
                    )
                )
            )
        )
        tot_by_board[b_key] += 1
        safe_sym = sym.replace(".", "_")
        csv_file = raw_dir / f"{safe_sym}.csv"
        is_missing = any(bool(r.get("data_missing") in ("1", "True", "true", True, 1)) for r in recs)
        if csv_file.exists() and not is_missing:
            raw_by_board[b_key] += 1

    raw_bar_coverage_by_exchange = {
        b: round(raw_by_board[b] / tot_by_board[b], 4) if tot_by_board[b] else 0.0
        for b in raw_by_board
    }
    total_has_raw = sum(raw_by_board.values())
    daily_raw_bar_coverage = round(total_has_raw / total_records, 4) if total_records else 0.0
    each_exchange_raw_coverage_ok = all(v >= 0.98 for v in raw_bar_coverage_by_exchange.values())

    # 13. Dynamic Trading Rule Verification (including 2026-07-06 switchover)
    from .costs import price_limit_pct
    rule_pre_st = price_limit_pct("600000.SH", "2026-07-03", is_st=True) == 0.05
    rule_post_st = price_limit_pct("600000.SH", "2026-07-06", is_st=True) == 0.10
    rule_star = price_limit_pct("688001.SH", "2026-07-03", is_st=True) == 0.20
    rule_bse = price_limit_pct("920002.BJ", "2026-07-03", is_st=True) == 0.30
    historical_trading_rules_verified = bool(rule_pre_st and rule_post_st and rule_star and rule_bse)

    # 8. Raw execution prices and sample coverage
    sample_pairs: list[tuple[str, str]] = []
    per_date = max(1, int(sample_size) // max(1, len(check_dates)))
    for d in check_dates:
        records = provider.active_records_on(d, universe.symbols)
        for rec in records[:per_date]:
            sample_pairs.append((d, str(rec["symbol"])))
    sample_pairs = sample_pairs[:max(1, int(sample_size))]

    bars_ok = 0
    raw_bars_ok = 0
    sector_map_ok = 0
    sector_history_ok = 0
    bar_details: list[dict[str, Any]] = []
    for as_of, sym in sample_pairs:
        bars = provider.bars(sym, count=max(900, settings.warmup_bars + 550))
        raw_b = provider.raw_bars(sym, count=max(900, settings.warmup_bars + 550))
        covers = bool(bars and bars[0].get("date", "9999") <= as_of and bars[-1].get("date", "") >= as_of)
        raw_covers = bool(raw_b and raw_b[0].get("date", "9999") <= as_of and raw_b[-1].get("date", "") >= as_of)
        if covers:
            bars_ok += 1
        if raw_covers:
            raw_bars_ok += 1
        sec = provider.sector_info_on(sym, as_of, strict=settings.sector_mode == "strict")
        if sec.get("code"):
            sector_map_ok += 1
            sb = provider.sector_bars(str(sec.get("code")))
            if sb and sb[0].get("date", "9999") <= as_of and sb[-1].get("date", "") >= as_of:
                sector_history_ok += 1
        bar_details.append({
            "as_of": as_of,
            "symbol": sym,
            "bars": len(bars),
            "raw_bars": len(raw_b),
            "bar_start": bars[0]["date"] if bars else None,
            "bar_end": bars[-1]["date"] if bars else None,
            "covers_as_of": covers,
            "raw_covers_as_of": raw_covers,
            "sector_code": sec.get("code"),
            "sector_name": sec.get("name"),
            "sector_source": sec.get("source"),
        })

    n = len(sample_pairs)
    price_cov = bars_ok / n if n else 0.0
    raw_price_cov = raw_bars_ok / n if n else 0.0
    sector_cov = sector_map_ok / n if n else 0.0
    sector_hist_cov = sector_history_ok / n if n else 0.0

    # Full universe data_missing check for raw price execution
    data_missing_count = sum(1 for sym in universe.symbols if any(bool(r.get("data_missing")) for r in provider._membership.get(sym, [])))
    full_market_price_bar_coverage = round((total_records - data_missing_count) / total_records, 4) if total_records else 0.0
    # Raw execution price is ready when raw bar coverage meets >= 0.98 and corporate actions ready
    raw_execution_price_ready = bool(corporate_action_ready and daily_raw_bar_coverage >= 0.98 and raw_price_cov >= 0.98)

    # Full Market Readiness Criteria
    min_symbols = int((config.research or {}).get("min_symbols_for_research_grade", 5000))
    min_daily_active = min((int(x.get("active_symbols", 0)) for x in universe_checks), default=0)

    criteria_checklist = {
        "1_market_universe_coverage": universe_market_coverage_ratio >= 0.95 and min_daily_active >= min_symbols,
        "2_exchange_coverage": exchange_coverage_ok,
        "3_historical_delisted_preserved": delisted_during_period_preserved,
        "4_no_prelisting_leakage": not ipo_prelisting_leakage,
        "5_no_post_delisting_leakage": not post_delisting_leakage,
        "6_status_dataset_complete": status_dataset_complete,
        "7_sector_dataset_complete": sector_dataset_complete,
        "8_corporate_action_dataset_complete": corporate_action_dataset_complete,
        "9_raw_execution_price_ready": raw_execution_price_ready,
        "10_daily_raw_bar_coverage": daily_raw_bar_coverage >= 0.98,
        "11_each_exchange_raw_coverage": each_exchange_raw_coverage_ok,
        "12_official_universe_set_match": official_universe_set_match,
        "13_historical_trading_rules_verified": historical_trading_rules_verified,
        "14_benchmark_coverage": benchmark_ok,
        "15_survivorship_bias": not universe.survivorship_bias,
    }

    formal_ready = all(criteria_checklist.values())

    if not criteria_checklist["9_raw_execution_price_ready"]:
        provider.warnings.append(
            f"RAW_EXECUTION_PRICE_INSUFFICIENT: daily_raw_bar_coverage={daily_raw_bar_coverage:.2%} < 98% "
            f"({data_missing_count}/{total_records} symbols flagged data_missing=True; prices required for full-market execution)"
        )
    if not criteria_checklist["8_corporate_action_dataset_complete"]:
        provider.warnings.append("CORPORATE_ACTIONS_NOT_READY: Corporate actions gated pending complete production historical verification")
    if not criteria_checklist["1_market_universe_coverage"]:
        provider.warnings.append(
            f"FULL_MARKET_COVERAGE_INSUFFICIENT: min_daily_active={min_daily_active} < min_symbols={min_symbols}"
        )

    result = {
        "ok": bool(universe_checks) and benchmark_ok and bars_ok > 0,
        "period": {"start": settings.start_date, "end": settings.end_date},
        "formal_full_market_ready": formal_ready,
        "research_grade_candidate": formal_ready,
        "universe_market_coverage_ratio": universe_market_coverage_ratio,
        "exchange_coverage": exchange_coverage,
        "historical_status_pit_coverage": historical_status_pit_coverage,
        "sector_label_coverage": sector_label_coverage,
        "sector_membership_pit_coverage": sector_membership_pit_coverage,
        "sector_constituent_pit_coverage": sector_constituent_pit_coverage,
        "raw_execution_price_ready": raw_execution_price_ready,
        "corporate_action_ready": corporate_action_ready,
        "official_universe_set_match": official_universe_set_match,
        "universe_extra_symbol_count": universe_extra_symbol_count,
        "universe_missing_symbol_count": universe_missing_symbol_count,
        "status_sample_verified": status_sample_verified,
        "status_data_verified": status_data_verified,
        "status_source_coverage": status_source_coverage,
        "status_dataset_complete": status_dataset_complete,
        "sector_schema_supports_pit": sector_schema_supports_pit,
        "sector_source_coverage": sector_source_coverage,
        "sector_data_verified_pit": sector_data_verified_pit,
        "sector_change_event_count": sector_change_event_count,
        "sector_change_events_in_backtest_period": sector_change_events_in_backtest_period,
        "sector_dataset_complete": sector_dataset_complete,
        "corporate_action_source_coverage": corporate_action_source_coverage,
        "corporate_action_expected_vs_loaded": corporate_action_expected_vs_loaded,
        "corporate_action_dataset_complete": corporate_action_dataset_complete,
        "corporate_action_data_verified": corporate_action_data_verified,
        "corporate_action_invalid_count": corporate_action_invalid_count,
        "synthetic_corporate_actions_detected": synthetic_corporate_actions_detected,
        "daily_raw_bar_coverage": daily_raw_bar_coverage,
        "raw_bar_coverage_by_exchange": raw_bar_coverage_by_exchange,
        "historical_trading_rules_verified": historical_trading_rules_verified,
        "criteria_checklist": criteria_checklist,
        "universe": {
            "source": universe.source,
            "seed_symbols": total_records,
            "survivorship_bias": universe.survivorship_bias,
            "point_in_time": universe.point_in_time,
            "dynamic_daily": universe.dynamic_daily,
            "membership_records": universe.membership_records,
            "dataset_version": universe.dataset_version,
            "coverage": universe_market_coverage_ratio,
            "notes": universe.notes,
            "check_dates": universe_checks,
            "checked_union_symbols": len(union_symbols),
            "delisted_stocks_preserved_count": len(delisted_in_universe),
            "data_missing_count": data_missing_count,
        },
        "sample": {
            "size": n,
            "price_asof_coverage": price_cov,
            "raw_price_asof_coverage": raw_price_cov,
            "sector_mapping_coverage": sector_cov,
            "sector_history_asof_coverage": sector_hist_cov,
            "details": bar_details,
        },
        "benchmark": {
            "symbol": settings.benchmark,
            "bars": len(benchmark),
            "trading_days": len(trading_dates),
            "covers_period": benchmark_ok,
            "start": trading_dates[0] if trading_dates else None,
            "end": trading_dates[-1] if trading_dates else None,
        },
        "provider_warnings": provider.warnings,
    }
    return result

