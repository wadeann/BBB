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
        "llm_api_calls": ((report.get("methodology") or {}).get("llm_filter_stats") or {}).get("api_calls", 0),
        "llm_failures": ((report.get("methodology") or {}).get("llm_filter_stats") or {}).get("failures", 0),
        "llm_candidate_error_rate": ((report.get("methodology") or {}).get("llm_filter_stats") or {}).get("candidate_error_rate", 0),
        "llm_avg_call_seconds": ((report.get("methodology") or {}).get("llm_filter_stats") or {}).get("avg_api_call_seconds", 0),
    }
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
    neutral_share = _neutral_sector_share(report)
    max_neutral = float(cfg.get("max_neutral_sector_share_for_research_grade", 0.85))
    if neutral_share > max_neutral:
        reasons.append(f"neutral_sector_trade_share={neutral_share:.3f} > {max_neutral:.3f}")
    dq = report.get("data_quality", {})
    if dq.get("sector_history_missing"):
        reasons.append("sector_history_missing")
    if not bool(dq.get("historical_sector_membership_point_in_time", False)):
        reasons.append("sector_membership_not_point_in_time")
    llm_stats = (report.get("methodology") or {}).get("llm_filter_stats") or {}
    if bool((report.get("settings") or {}).get("llm_filter_enabled")):
        failures = int(llm_stats.get("failures", 0) or 0)
        error_candidates = int(llm_stats.get("error_candidates", 0) or 0)
        reviewed = int(llm_stats.get("candidates_reviewed", 0) or 0)
        if failures > 0 or error_candidates > 0:
            reasons.append(f"llm_gate_transport_or_schema_errors={error_candidates}/{reviewed}_candidates;calls_failed={failures}")
    llm_stats = (report.get("methodology") or {}).get("llm_filter_stats") or {}
    llm_enabled = bool((report.get("settings") or {}).get("llm_filter_enabled"))
    llm_valid = (not llm_enabled) or (int(llm_stats.get("failures", 0) or 0) == 0 and int(llm_stats.get("error_candidates", 0) or 0) == 0)
    return {
        "grade": "RESEARCH_GRADE" if not reasons else "DIAGNOSTIC_ONLY",
        "reasons": reasons,
        "tested_symbols": tested,
        "neutral_sector_trade_share": neutral_share,
        "survivorship_bias": bool(universe.survivorship_bias) if universe else None,
        "llm_experiment_valid": llm_valid,
        "llm_filter_stats": llm_stats if llm_enabled else None,
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
                llm_filter = HistoricalLLMFilter(
                    self.root, self.llm, model_id=model_id, use_cache=True,
                    anonymize_symbol=settings.llm_filter_anonymize_symbol,
                    batch_size=settings.llm_filter_batch_size,
                    payload_mode=settings.llm_filter_payload_mode,
                    max_bars=settings.llm_filter_max_bars,
                )  # type: ignore[arg-type]
            report = BacktestEngine(self.config, provider, settings, llm_filter=llm_filter).run(symbols)
            if universe:
                report["universe"] = {"source": universe.source, "survivorship_bias": universe.survivorship_bias, "notes": universe.notes, "symbols": len(symbols), "point_in_time": universe.point_in_time, "membership_records": universe.membership_records}
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
            "universe": ({"source": universe.source, "survivorship_bias": universe.survivorship_bias, "notes": universe.notes, "symbols": len(symbols), "point_in_time": universe.point_in_time, "membership_records": universe.membership_records} if universe else {"source": "explicit_symbols", "symbols": len(symbols)}),
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
    """Check whether the historical data layer is suitable before an expensive suite."""
    settings = settings_from(config, overrides or {})
    provider = HistoricalDataProvider(config.project_root, mcp, use_cache=settings.cache)
    universe = provider.load_universe_for_period(
        settings.start_date, settings.end_date, settings.universe_file,
        max_universe=settings.max_universe, mode=settings.universe_mode,
    )
    sample = universe.symbols[:max(1, int(sample_size))]
    bars_ok = 0
    sector_map_ok = 0
    sector_history_ok = 0
    bar_details: list[dict[str, Any]] = []
    for sym in sample:
        bars = provider.bars(sym, count=max(900, settings.warmup_bars + 550))
        covers = bool(bars and bars[0].get("date", "9999") <= settings.start_date and bars[-1].get("date", "") >= settings.end_date)
        if covers:
            bars_ok += 1
        sec = provider.sector_info(sym)
        if sec.get("code"):
            sector_map_ok += 1
            sb = provider.sector_bars(str(sec.get("code")))
            if sb and sb[0].get("date", "9999") <= settings.start_date and sb[-1].get("date", "") >= settings.end_date:
                sector_history_ok += 1
        bar_details.append({
            "symbol": sym,
            "bars": len(bars),
            "bar_start": bars[0]["date"] if bars else None,
            "bar_end": bars[-1]["date"] if bars else None,
            "covers_period": covers,
            "sector_code": sec.get("code"),
            "sector_source": sec.get("source"),
            "sector_diagnostic": sec.get("diagnostic"),
        })
    benchmark = provider.bars(settings.benchmark, count=max(900, settings.warmup_bars + 550))
    benchmark_ok = bool(benchmark and benchmark[0].get("date", "9999") <= settings.start_date and benchmark[-1].get("date", "") >= settings.end_date)
    result = {
        "ok": bool(universe.symbols) and benchmark_ok and bars_ok > 0,
        "period": {"start": settings.start_date, "end": settings.end_date},
        "universe": {
            "source": universe.source,
            "symbols": len(universe.symbols),
            "survivorship_bias": universe.survivorship_bias,
            "point_in_time": universe.point_in_time,
            "membership_records": universe.membership_records,
            "notes": universe.notes,
        },
        "sample": {
            "size": len(sample),
            "price_period_coverage": bars_ok / len(sample) if sample else 0.0,
            "sector_mapping_coverage": sector_map_ok / len(sample) if sample else 0.0,
            "sector_history_period_coverage": sector_history_ok / len(sample) if sample else 0.0,
            "details": bar_details,
        },
        "benchmark": {
            "symbol": settings.benchmark,
            "bars": len(benchmark),
            "covers_period": benchmark_ok,
            "start": benchmark[0]["date"] if benchmark else None,
            "end": benchmark[-1]["date"] if benchmark else None,
        },
        "provider_warnings": provider.warnings,
        "research_grade_candidate": bool(
            universe.point_in_time and not universe.survivorship_bias and
            len(universe.symbols) >= int((config.research or {}).get("min_symbols_for_research_grade", 500)) and
            bars_ok / len(sample) >= 0.9 if sample else False
        ),
    }
    return result
