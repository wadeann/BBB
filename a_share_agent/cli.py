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

    s = sub.add_parser("research-latest", help="show latest research suite feedback bundle path")
    s.set_defaults(func=cmd_research_latest)

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
