from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .diagnostics import write_diagnostic_report
from .runtime_logging import configure_runtime_logging
from .factory import create_llm_client, create_mcp_invoker, create_runtime
from .mcp.fake import FakeMCPInvoker
from .mcp.http import StreamableHTTPMCPInvoker
from .runtime import AgentRuntime
from .utils import as_trade_date, redact
from .worker import BackgroundWorker
from .backtest.service import BacktestService, settings_from
from .backtest.data import HistoricalDataProvider
from .backtest.engine import BacktestEngine
from .backtest.report import BacktestReportWriter
from .backtest.walk_forward_service import run_stability
from .backtest.research import ResearchLab, run_research_preflight


def _project_root(arg: str | None) -> Path:
    return Path(arg or Path(__file__).resolve().parents[1]).resolve()


def _emit_diagnostic(root: Path, report_type: str, payload: dict, *, ok: bool) -> dict:
    paths = write_diagnostic_report(root, report_type, payload, ok=ok)
    out = redact(dict(payload))
    out["diagnostic_files"] = paths
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return out


def _runtime(args, *, force_fake: bool = False, include_llm: bool = True) -> AgentRuntime:
    backend = "fake" if force_fake else getattr(args, "backend", None)
    return create_runtime(_project_root(args.root), backend=backend, include_llm=include_llm)


def _seed_demo(rt: AgentRuntime) -> None:
    """Seed deterministic paper/demo data once per trade date. Safe to call repeatedly."""
    trade_date = as_trade_date()
    if rt.audit.index.count(trade_date) > 0:
        return
    for phase in ("PREOPEN_PRECHECK", "PREOPEN_CONTEXT", "EOD_UNIVERSE_SCAN"):
        rt.run_phase(phase)
    signal = {"symbol":"600000.SH","direction":"BUY","strategy_id":"ma60_breakout_retest","signal_version":"v1","decision":"ENTRY_CANDIDATE","score":84,"limit_price":10.0,"stop":9.6,"quantity":1000,"reason":"web demo signal"}
    route = {"route_id":"RISK_ON_STRONG_SECTOR","position_multiplier":1.0}
    rt.execute_signal("ENTRY_WINDOW_AM", signal, route)
    rt.run_phase("CLOSE_RECONCILE")
    rt.run_phase("DAILY_REVIEW")


def cmd_demo(args) -> None:
    rt = _runtime(args, force_fake=True)
    for phase in ("PREOPEN_PRECHECK", "PREOPEN_CONTEXT", "EOD_UNIVERSE_SCAN"):
        print(f"\n== {phase} ==")
        print(json.dumps(rt.run_phase(phase), ensure_ascii=False, indent=2)[:4000])
    print("\n== PAPER EXECUTION ==")
    signal = {"symbol":"600000.SH","direction":"BUY","strategy_id":"ma60_breakout_retest","signal_version":"v1","decision":"ENTRY_CANDIDATE","score":84,"limit_price":10.0,"stop":9.6,"quantity":1000,"reason":"demo signal"}
    route = {"route_id":"RISK_ON_STRONG_SECTOR","position_multiplier":1.0}
    print(json.dumps(rt.execute_signal("ENTRY_WINDOW_AM", signal, route), ensure_ascii=False, indent=2))
    print("\n== CLOSE / REVIEW / FINALIZE ==")
    for phase in ("CLOSE_RECONCILE", "DAILY_REVIEW", "AUDIT_FINALIZE"):
        print(json.dumps(rt.run_phase(phase), ensure_ascii=False, indent=2)[:4000])
    print(f"\nAudit root: {rt.audit.audit_root}")


def cmd_phase(args) -> None:
    rt = _runtime(args)
    print(json.dumps(rt.run_phase(args.phase), ensure_ascii=False, indent=2))


def cmd_verify(args) -> None:
    rt = _runtime(args, include_llm=False)
    print(json.dumps(rt.replay.verify_hash_chain(args.date or as_trade_date()), ensure_ascii=False, indent=2))


def cmd_replay(args) -> None:
    rt = _runtime(args, include_llm=False)
    events = rt.replay.exact(args.date, trace_id=args.trace, symbol=args.symbol)
    print(json.dumps(events, ensure_ascii=False, indent=2))


def cmd_worker(args) -> None:
    rt = _runtime(args)
    worker = BackgroundWorker(rt, poll_seconds=args.poll_seconds)
    if args.once:
        if not args.skip_recovery:
            print(json.dumps(worker.startup_recovery(), ensure_ascii=False, indent=2))
        print(json.dumps(worker.tick(), ensure_ascii=False, indent=2, default=str))
        print(json.dumps({"notifications": rt.notifications.drain_once()}, ensure_ascii=False, indent=2))
        return
    worker.run_forever(max_runtime_seconds=args.max_runtime_seconds, run_recovery=not args.skip_recovery)


def cmd_worker_status(args) -> None:
    rt = _runtime(args, include_llm=False)
    payload = {
        "status": rt.worker_state.latest_status(),
        "phase_history": rt.worker_state.phase_history(as_trade_date(), 50),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_web(args) -> None:
    import uvicorn
    from .web.app import create_app

    # Web is read-only and intentionally does not own an LLM client. The background
    # worker is the only long-running process allowed to perform agent reasoning.
    rt = _runtime(args, include_llm=False)
    if args.seed_demo:
        if getattr(args, "backend", None) == "production" or rt.config.runtime.get("backend") == "production":
            raise SystemExit("--seed-demo is allowed only with fake backend")
        _seed_demo(rt)
    app = create_app(rt)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


def cmd_mcp_probe(args) -> None:
    root = _project_root(args.root)
    services = args.service or ["intel", "risk", "exec"]
    try:
        cfg = load_config(root)
        mcp = create_mcp_invoker(cfg, backend="production")
        if not isinstance(mcp, StreamableHTTPMCPInvoker):
            raise RuntimeError("mcp-probe requires production MCP configuration")
        out = {service: mcp.probe(service) for service in services}
        ok = all(item.get("ok") and item.get("catalog_match", True) for item in out.values())
        _emit_diagnostic(root, "mcp_probe", {"services": services, "results": out}, ok=ok)
    except Exception as exc:
        ok = False
        _emit_diagnostic(root, "mcp_probe", {"services": services, "error": str(exc)}, ok=False)
    if not ok:
        raise SystemExit(2)


def cmd_llm_probe(args) -> None:
    root = _project_root(args.root)
    try:
        cfg = load_config(root)
        llm = create_llm_client(cfg, required=True)
        if llm is None or not hasattr(llm, "probe"):
            raise RuntimeError("llm-probe requires a configured OpenAI-compatible LLM client")
        result = llm.probe()
        ok = bool(result.get("ok"))
        _emit_diagnostic(root, "llm_probe", result, ok=ok)
    except Exception as exc:
        ok = False
        _emit_diagnostic(root, "llm_probe", {"error": str(exc)}, ok=False)
    if not ok:
        raise SystemExit(2)


def cmd_preflight(args) -> None:
    root = _project_root(args.root)
    services = args.service or ["intel", "risk", "exec"]
    result: dict[str, object] = {
        "backend": "production",
        "mcp": {},
        "llm": None,
    }
    try:
        cfg = load_config(root)
        result["mode"] = cfg.mode
        result["allow_real_execution"] = bool(cfg.runtime.get("safety", {}).get("allow_real_execution", False))
        try:
            mcp = create_mcp_invoker(cfg, backend="production")
            if not isinstance(mcp, StreamableHTTPMCPInvoker):
                raise RuntimeError("production MCP adapter unavailable")
            result["mcp"] = {service: mcp.probe(service) for service in services}
        except Exception as exc:
            result["mcp"] = {"_adapter": {"ok": False, "error": str(exc)}}
        try:
            llm = create_llm_client(cfg, required=True)
            result["llm"] = llm.probe() if llm is not None and hasattr(llm, "probe") else {"ok": False, "error": "probe unavailable"}
        except Exception as exc:
            result["llm"] = {"ok": False, "error": str(exc)}
    except Exception as exc:
        result["config_error"] = str(exc)

    mcp_values = [v for v in result.get("mcp", {}).values() if isinstance(v, dict)] if isinstance(result.get("mcp"), dict) else []
    mcp_ok = bool(mcp_values) and all(item.get("ok") and item.get("catalog_match", True) for item in mcp_values)
    llm_ok = bool(isinstance(result.get("llm"), dict) and result["llm"].get("ok"))
    ok = mcp_ok and llm_ok
    _emit_diagnostic(root, "preflight", result, ok=ok)
    if not ok:
        raise SystemExit(2)



def cmd_notification_probe(args) -> None:
    root = _project_root(args.root)
    try:
        rt = _runtime(args, include_llm=False)
        result = rt.notifications.probe(args.channel)
        ok = bool(result.get("ok"))
        _emit_diagnostic(root, "notification_probe", result, ok=ok)
    except Exception as exc:
        ok = False
        _emit_diagnostic(root, "notification_probe", {"channel": args.channel, "error": str(exc)}, ok=False)
    if not ok:
        raise SystemExit(2)


def cmd_notification_status(args) -> None:
    rt = _runtime(args, include_llm=False)
    print(json.dumps(rt.notifications.recent(args.limit), ensure_ascii=False, indent=2, default=str))


def cmd_backtest(args) -> None:
    root=_project_root(args.root); cfg=load_config(root)
    backend=getattr(args,"backend",None) or cfg.runtime.get("backend","fake")
    mcp=create_mcp_invoker(cfg,backend=backend)
    overrides={k:v for k,v in {
        "start_date":args.start,"end_date":args.end,"initial_cash":args.cash,"benchmark":args.benchmark,
        "min_score":args.min_score,"max_universe":args.max_universe,"universe_file":args.universe_file,
        "slippage_bps":args.slippage_bps,"sector_mode":args.sector_mode,"universe_mode":getattr(args, "universe_mode", None)
    }.items() if v is not None}
    settings=settings_from(cfg,overrides); provider=HistoricalDataProvider(root,mcp,use_cache=not args.no_cache)
    if getattr(args, "require_research_grade", False):
        preflight = run_research_preflight(cfg, mcp, overrides=overrides)
        if not bool(preflight.get("formal_full_market_ready")):
            sys.stderr.write("BLOCKED: formal_full_market_ready is false; research grade not ready.\n")
            raise SystemExit(3)
    symbols=list(args.symbol or [])
    uni=None
    if not symbols:
        uni=provider.load_universe_for_period(settings.start_date,settings.end_date,settings.universe_file,max_universe=settings.max_universe,mode=settings.universe_mode); symbols=uni.symbols
    if not symbols: raise SystemExit("回测股票池为空：请填写 data/backtest/universe.txt，或使用 production backend 获取股票池")
    def run_one(ss):
        report=BacktestEngine(cfg,provider,ss).run(symbols)
        if uni: report["universe"]={"source":uni.source,"survivorship_bias":uni.survivorship_bias,"notes":uni.notes,"seed_symbols":len(symbols),"tested_union_symbols":report.get("coverage",{}).get("tested_symbols",0),"point_in_time":uni.point_in_time,"membership_records":uni.membership_records,"dynamic_daily":uni.dynamic_daily,"dataset_version":uni.dataset_version,"coverage":uni.coverage}
        return report
    report=run_one(settings); path=BacktestReportWriter(root).write(report)
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str))



#
# Strategy-family definitions and per-family backtesting.
#

STRATEGY_FAMILIES: dict[str, list[str]] = {
    "trend_breakout": ["triple_golden_cross", "ma_convergence_breakout", "high_volume_breakout"],
    "trend_pullback": ["ma60_breakout_retest", "ma5_momentum_pullback", "single_bull_hold", "low_volume_support_bull"],
    "rebound_reversal": [],
    "pattern_confirmation": ["long_bull_day7"],
    "exit_defensive": ["shooting_star_high", "volume_price_divergence", "ma20_break", "ma_bearish_cut"],
}


def _fmt_month(month_label: str) -> str:
    parts = month_label.split("-")
    return f"{parts[0][2:]}-{parts[1]}" if len(parts) >= 2 else month_label


def cmd_strategy_backtest(args) -> None:
    root = _project_root(args.root)
    cfg = load_config(root)
    mcp = create_mcp_invoker(cfg, backend=getattr(args, "backend", None) or cfg.runtime.get("backend", "production"))
    overrides = {k: v for k, v in {
        "start_date": args.start, "end_date": args.end, "min_score": 70.0,
        "cache": False, "route_mode": "disabled", "sector_mode": "historical_or_neutral",
    }.items() if v is not None}
    settings = settings_from(cfg, overrides)
    provider = HistoricalDataProvider(root, mcp, use_cache=False)
    symbols = list(args.symbol or [])
    uni = None
    if not symbols:
        uni = provider.load_universe_for_period(settings.start_date, settings.end_date,
            settings.universe_file, max_universe=63, mode=settings.universe_mode)
        symbols = uni.symbols
    if not symbols:
        raise SystemExit("回测股票池为空")
    for family_name, pattern_names in STRATEGY_FAMILIES.items():
        print(f"\n=== 策略: {family_name} ===")
        if not pattern_names:
            print("(无信号模式 — 跳过)")
            continue
        ss = settings_from(cfg, {**overrides, "enabled_strategies": pattern_names, "cache": False})
        report = BacktestEngine(cfg, provider, ss).run(symbols)
        metrics = report.get("metrics", {})
        trades = report.get("trades", [])
        sells = [t for t in trades if t.get("direction") == "SELL" and t.get("pnl") is not None]
        tr = float(metrics.get("total_return", 0))
        wr = float(metrics.get("win_rate", 0))
        sh = float(metrics.get("sharpe", 0))
        dd = float(metrics.get("max_drawdown", 0))
        print(f"总收益: {tr * 100:.2f}%")
        print(f"平仓: {len(sells)} 笔 | 胜率: {wr * 100:.1f}%")
        print(f"Sharpe: {sh:.2f} | 最大回撤: {dd * 100:.2f}%")
        monthly_items = [f"{_fmt_month(m.get('month', '?'))}:{float(m.get('return', 0)) * 100:.1f}%" for m in report.get("monthly_returns", [])]
        print(f"月收益: [{'  '.join(monthly_items)}]")
        print(f"\n交易记录:")
        print(f"{'日期':12s} {'方向':6s} {'股票代码':12s} {'价格':>8s} {'策略':24s} {'评分':>6s} {'盈亏%':>7s} {'持仓日':>4s} {'退出原因':20s}")
        print("-" * 108)
        for t in trades:
            td = str(t.get("trade_date", "?"))
            dr = str(t.get("direction", "?"))
            sy = str(t.get("symbol", "?"))
            pr = float(t.get("price", 0))
            si = str(t.get("strategy_id") or "?")[:24]
            sc = float(t.get("score", 0) or 0)
            pp = float(t.get("pnl_pct", 0) or 0)
            hd = int(t.get("holding_days", 0) or 0)
            er = str(t.get("exit_reason") or "?")[:20]
            print(f"{td:12s} {dr:6s} {sy:12s} {pr:>8.2f} {si:24s} {sc:>6.0f} {pp * 100:>7.2f}% {hd:>4d} {er:20s}")
        print()


def cmd_walk_forward_stability(args) -> None:
    root=_project_root(args.root); cfg=load_config(root)
    import yaml
    config_path=Path(args.config)
    if not config_path.is_absolute(): config_path=root/args.config
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")
    with config_path.open("r",encoding="utf-8") as fh:
        wf_cfg=yaml.safe_load(fh) or {}
    wf_cfg.setdefault("start_date",cfg.backtest.get("start_date","2024-10-01"))
    wf_cfg.setdefault("end_date",cfg.backtest.get("end_date","2026-09-30"))
    wf_cfg.setdefault("train_months",12); wf_cfg.setdefault("test_months",3); wf_cfg.setdefault("step_months",3)
    wf_cfg.setdefault("warmup_bars",260); wf_cfg.setdefault("universe",[])
    wf_cfg.setdefault("settings",{}); wf_cfg.setdefault("stability_thresholds",{})
    output=Path(args.output)
    if not output.is_absolute(): output=root/args.output
    try:
        result=run_stability(cfg,wf_cfg,output,run_id=args.run_id)
    except FileExistsError as exc:
        sys.stderr.write(f"COLLISION: {exc}\n"); raise SystemExit(3)
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n"); raise SystemExit(3)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    status=result.get("status","FAILED")
    if status=="COMPLETED": raise SystemExit(0)
    elif status=="PARTIAL": raise SystemExit(1)
    else: raise SystemExit(2)


def cmd_research_preflight(args) -> None:
    root=_project_root(args.root); cfg=load_config(root)
    backend=getattr(args,"backend",None) or cfg.runtime.get("backend","fake")
    mcp=create_mcp_invoker(cfg,backend=backend)
    overrides={k:v for k,v in {
        "start_date":args.start,"end_date":args.end,"max_universe":args.max_universe,"universe_file":args.universe_file,
        "universe_mode":args.universe_mode
    }.items() if v is not None}
    result=run_research_preflight(cfg,mcp,overrides=overrides,sample_size=args.sample_size)
    _emit_diagnostic(root,"research_preflight",result,ok=bool(result.get("ok")))
    if not result.get("ok"):
        raise SystemExit(2)
    if getattr(args, "require_research_grade", False) and not bool(result.get("formal_full_market_ready")):
        raise SystemExit(3)

def cmd_research_suite(args) -> None:
    root=_project_root(args.root); cfg=load_config(root)
    backend=getattr(args,"backend",None) or cfg.runtime.get("backend","fake")
    mcp=create_mcp_invoker(cfg,backend=backend)
    include_llm=bool(args.include_llm)
    llm=create_llm_client(cfg,required=True,profile="research") if include_llm else None
    overrides={k:v for k,v in {
        "start_date":args.start,"end_date":args.end,"initial_cash":args.cash,"benchmark":args.benchmark,
        "min_score":args.min_score,"max_universe":args.max_universe,"universe_file":args.universe_file,
        "slippage_bps":args.slippage_bps,"sector_mode":args.sector_mode,"universe_mode":args.universe_mode
    }.items() if v is not None}
    if getattr(args, "require_research_grade", False):
        preflight = run_research_preflight(cfg, mcp, overrides=overrides)
        if not bool(preflight.get("formal_full_market_ready")):
            sys.stderr.write("BLOCKED: formal_full_market_ready is false; full-market research grade not ready.\n")
            raise SystemExit(3)
    lab=ResearchLab(cfg,mcp,llm)
    try:
        summary=lab.run(overrides=overrides,symbols=list(args.symbol or []),include_llm=include_llm,experiment_ids=list(args.experiment or []))
    except RuntimeError as exc:
        if "FORMAL_FULL_MARKET_GATE_BLOCKED" in str(exc):
            sys.stderr.write(f"BLOCKED: {exc}\n")
            raise SystemExit(3)
        raise
    print(json.dumps({
        "suite_id":summary["suite_id"],
        "feedback_bundle":summary.get("feedback_bundle"),
        "report_dir":str(root/"data"/"research"/"runs"/summary["suite_id"]),
        "experiments":summary.get("experiments"),
    },ensure_ascii=False,indent=2,default=str))


def cmd_research_latest(args) -> None:
    root=_project_root(args.root); p=root/"data"/"research"/"runs"/"latest.json"
    if not p.exists():
        raise SystemExit("no research suite has been run yet")
    print(p.read_text(encoding="utf-8"))

def cmd_backtest_list(args) -> None:
    root=_project_root(args.root); print(json.dumps(BacktestReportWriter(root).list_runs(args.limit),ensure_ascii=False,indent=2))

def cmd_stock_pick(args) -> None:
    import csv, json, sys
    from datetime import datetime
    from .strategy.signal_engine import DeterministicSignalEngine
    from .strategy.router import StrategyRouter
    from .strategy.resonance import sector_resonance_filter
    from .backtest.scoring import deterministic_score
    from .backtest.regime import market_context_from_benchmarks, sector_context_from_history
    root = _project_root(args.root)
    # Build sector lookup from security_master
    _sector_csv_path = root / "data/backtest/security_master.csv"
    _sector_map = {}
    if _sector_csv_path.exists():
        with open(_sector_csv_path, "r", encoding="utf-8-sig") as _f:
            for _r in csv.DictReader(_f):
                _sym = _r.get("symbol") or _r.get("\ufeffsymbol")
                if _sym:
                    _sector_map[_sym] = {"name": _r.get("industry_name","?"), "code": _r.get("industry_code","?"), "stock_name": _r.get("name","?")}
    cfg = load_config(root)
    mcp = create_mcp_invoker(cfg, backend=getattr(args, "backend", None) or cfg.runtime.get("backend", "production"))
    provider = HistoricalDataProvider(root, mcp, use_cache=not args.no_cache)
    settings = settings_from(cfg, {"start_date":"2026-07-01","end_date":"2026-09-30","max_universe":args.max_universe or 63})
    if args.symbol:
        symbols = list(args.symbol)
    else:
        uni = provider.load_universe_for_period(settings.start_date, settings.end_date,
            settings.universe_file, max_universe=settings.max_universe, mode=settings.universe_mode)
        symbols = uni.symbols
    benchmark_symbols = ["000300.SH", "000852.SH", "399006.SZ"]
    benchmark_series = {sym: provider.bars(sym, count=250) for sym in benchmark_symbols}
    benchmark = benchmark_series.get("000300.SH", next((v for v in benchmark_series.values() if v), []))
    today = benchmark[-1]["date"] if benchmark else datetime.now().strftime("%Y-%m-%d")
    market = market_context_from_benchmarks(benchmark_series, today)
    signal_engine = DeterministicSignalEngine()
    router = StrategyRouter(cfg.strategy_router)
    candidates = []
    for sym in symbols:
        bars = provider.bars(sym, count=250)
        if len(bars) < 60:
            continue
        active = provider.eligible_on(sym, today) if hasattr(provider, "eligible_on") else True
        if not active:
            continue
        hits = signal_engine.scan(bars, market_regime=market.get("market_regime","unknown"),
                                  sector_strength="unknown")
        if not hits:
            continue
        primaries = [h for h in hits if h.get("strength")=="primary"]
        if not primaries:
            continue
        info = {}
        try:
            if hasattr(provider, "sector_info"):
                raw = provider.sector_info(sym) or {}
                # Only use provider data if it has real sector info
                if raw.get("name") or raw.get("code"):
                    info = raw
        except Exception:
            pass
        if not info.get("name") and sym in _sector_map:
            info = _sector_map[sym]
        sector_bars_list = []
        if info.get("code"):
            try:
                sb = provider.sector_bars(str(info["code"])) if hasattr(provider, "sector_bars") else []
                sector_bars_list = sb or []
            except Exception:
                sector_bars_list = []
        sector_ctx = sector_context_from_history(sector_bars_list, today, name=info.get("name"))
        route = router.route(market_context=market, sector_context=sector_ctx)
        score, breakdown = deterministic_score(hits, bars, market, sector_ctx)
        effective_threshold = settings.min_score + route.get("candidate_threshold_delta", 0)
        if score < effective_threshold:
            continue
        candidates.append({
            "symbol": sym,
            "score": score,
            "breakdown": breakdown,
            "strategy": primaries[0].get("signal", "?"),
            "evidence": primaries[0].get("evidence", {}),
            "sector": info.get("name") or "?",
            "sector_code": info.get("code", "?"),
            "stock_name": _sector_map.get(sym, {}).get("stock_name") or "?",
            "route_id": route.get("route_id", "?"),
            "route_mult": route.get("position_multiplier", 0),
            "market_regime": market.get("market_regime", "?"),
            "sector_strength": sector_ctx.get("sector_strength", "?"),
            "regime": market.get("regime", "?"),
        })
    # Apply sector resonance annotation
    for c in candidates:
        res = sector_resonance_filter(
            symbol=c["symbol"],
            sector_code=c.get("sector_code"),
            sector_name=c.get("sector"),
            all_candidates=candidates,
            as_of=today,
            provider=provider,
        )
        c["resonance"] = res
        c["sector_strength_label"] = res.get("reason", "?")
        c["sector_peer_count"] = res.get("sector_strength", 0)

    if args.json:
        print(json.dumps({"as_of": today, "market": market, "candidates": candidates,
            "total_universe": len(symbols), "candidate_count": len(candidates)}, ensure_ascii=False, indent=2))
        return
    print("-" * 140)
    print(f"{'评分':>4}  {'股票':10s} {'名称':12s} {'板块':16s} {'共振':14s} {'策略':28s} {'信号详情':25s} {'路由':15s}")
    print("-" * 140)
    for c in candidates[:args.limit]:
        ev = c.get("evidence", {})
        detail = " ".join(f"{k}={v}" for k,v in list(ev.items())[:3])
        resonance = c.get("sector_strength_label", "?")
        print(f"{c['score']:4.0f}  {c['symbol']:10s} {c['stock_name'][:12]:12s} {c['sector'][:16]:16s} {resonance[:14]:14s} {c['strategy']:28s} {detail[:25]:25s} {c['route_id']:15s}")
    print(f"总池 {len(symbols)} 只 | 选股 {len(candidates)} 只")

def main() -> None:
    p = argparse.ArgumentParser(prog="a-share-agent")
    p.add_argument("--root", help="runtime project root")
    p.add_argument("--backend", choices=["fake", "production"], help="override config/runtime.yaml backend")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("demo", help="run safe fake-MCP end-to-end demo"); s.set_defaults(func=cmd_demo)
    s = sub.add_parser("phase", help="run one phase"); s.add_argument("phase"); s.set_defaults(func=cmd_phase)
    s = sub.add_parser("verify-audit"); s.add_argument("--date"); s.set_defaults(func=cmd_verify)
    s = sub.add_parser("replay"); s.add_argument("date"); s.add_argument("--trace"); s.add_argument("--symbol"); s.set_defaults(func=cmd_replay)

    s = sub.add_parser("worker", help="run the persistent background scheduler")
    s.add_argument("--poll-seconds", type=float, default=None)
    s.add_argument("--max-runtime-seconds", type=float, default=None, help="test/debug only")
    s.add_argument("--skip-recovery", action="store_true")
    s.add_argument("--once", action="store_true", help="run one scheduler tick then exit")
    s.set_defaults(func=cmd_worker)

    s = sub.add_parser("worker-status", help="show persistent worker heartbeat and phase history")
    s.set_defaults(func=cmd_worker_status)

    s = sub.add_parser("web", help="launch the read-only web console")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.add_argument("--log-level", default="info"); s.add_argument("--seed-demo", action="store_true")
    s.set_defaults(func=cmd_web)

    s = sub.add_parser("mcp-probe", help="initialize production MCP endpoints and list available tools")
    s.add_argument("--service", action="append", choices=["intel", "risk", "exec", "jin10"], help="repeatable; default intel/risk/exec")
    s.set_defaults(func=cmd_mcp_probe)

    s = sub.add_parser("llm-probe", help="test the configured OpenAI-compatible LLM with a JSON health check")
    s.set_defaults(func=cmd_llm_probe)

    s = sub.add_parser("preflight", help="probe production MCP catalog and LLM before starting worker/web")
    s.add_argument("--service", action="append", choices=["intel", "risk", "exec", "jin10"], help="repeatable; default intel/risk/exec")
    s.set_defaults(func=cmd_preflight)

    s = sub.add_parser("notification-probe", help="send a harmless test notification through a configured channel")
    s.add_argument("--channel", choices=["feishu"], default="feishu")
    s.set_defaults(func=cmd_notification_probe)

    s = sub.add_parser("notification-status", help="show recent persistent notification delivery records")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_notification_status)

    s = sub.add_parser("backtest", help="run reproducible historical daily-bar backtest")
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--cash", type=float); s.add_argument("--benchmark")
    s.add_argument("--symbol", action="append", help="repeatable explicit universe symbol")
    s.add_argument("--universe-file"); s.add_argument("--max-universe", type=int); s.add_argument("--min-score", type=float)
    s.add_argument("--slippage-bps", type=float); s.add_argument("--sector-mode", choices=["strict","historical_or_neutral","disabled"])
    s.add_argument("--universe-mode", choices=["strict_point_in_time","prefer_point_in_time","current_fallback","file"])
    s.add_argument("--no-cache", action="store_true"); s.add_argument("--walk-forward", action="store_true")
    s.add_argument("--train-months", type=int, default=12); s.add_argument("--test-months", type=int, default=3)
    s.add_argument("--require-research-grade", action="store_true", help="exit nonzero unless full-market PIT data is research-grade ready")
    s.set_defaults(func=cmd_backtest)

    s = sub.add_parser("stock-pick", help="run live stock selection scan against production MCP")
    s.add_argument("--symbol", action="append", help="explicit symbols")
    s.add_argument("--max-universe", type=int, default=63)
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--json", action="store_true", help="JSON output")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--backend", choices=["fake", "production"], help="override default backend")
    s.set_defaults(func=cmd_stock_pick)

    s = sub.add_parser("research-preflight", help="check historical universe/price/sector coverage before research suite")
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--universe-file"); s.add_argument("--max-universe", type=int)
    s.add_argument("--universe-mode", choices=["strict_point_in_time","prefer_point_in_time","current_fallback","file"])
    s.add_argument("--sample-size", type=int, default=30)
    s.add_argument("--require-research-grade", action="store_true", help="exit nonzero unless full-market PIT data is research-grade ready")
    s.set_defaults(func=cmd_research_preflight)

    s = sub.add_parser("research-suite", help="run standardized A/B and ablation backtest experiments")
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--cash", type=float); s.add_argument("--benchmark")
    s.add_argument("--symbol", action="append", help="repeatable explicit universe symbol")
    s.add_argument("--universe-file"); s.add_argument("--max-universe", type=int); s.add_argument("--min-score", type=float)
    s.add_argument("--slippage-bps", type=float); s.add_argument("--sector-mode", choices=["strict","historical_or_neutral","disabled"])
    s.add_argument("--universe-mode", choices=["strict_point_in_time","prefer_point_in_time","current_fallback","file"])
    s.add_argument("--experiment", action="append", help="repeatable experiment id; default runs all configured deterministic experiments")
    s.add_argument("--include-llm", action="store_true", help="also run configured historical LLM gate experiments; can incur API cost")
    s.add_argument("--require-research-grade", action="store_true", help="exit nonzero unless full-market PIT data is research-grade ready")
    s.set_defaults(func=cmd_research_suite)

    s = sub.add_parser("strategy-backtest", help="run per-strategy-family backtest for Q3 2026")
    s.add_argument("--start", default="2026-07-01")
    s.add_argument("--end", default="2026-09-30")
    s.add_argument("--symbol", action="append", help="explicit universe symbol (repeatable)")
    s.set_defaults(func=cmd_strategy_backtest)

    s = sub.add_parser("backtest-list", help="list saved backtest reports")
    s.add_argument("--limit", type=int, default=50); s.set_defaults(func=cmd_backtest_list)

    args = p.parse_args()
    root = _project_root(args.root)
    logger = configure_runtime_logging(root, args.cmd)
    logger.info("command_start cmd=%s backend=%s", args.cmd, getattr(args, "backend", None))
    try:
        args.func(args)
        logger.info("command_complete cmd=%s", args.cmd)
    except SystemExit:
        logger.info("command_exit cmd=%s", args.cmd)
        raise
    except Exception:
        logger.exception("command_failed cmd=%s", args.cmd)
        raise


if __name__ == "__main__":
    main()

