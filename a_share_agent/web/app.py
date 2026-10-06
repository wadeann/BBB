from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..market.dashboard import DashboardService
from ..market.workbench import WorkbenchService
from ..runtime import AgentRuntime
from ..event_stream import AuditSSEStream
from ..utils import as_trade_date
from ..backtest.service import BacktestService


def create_app(runtime: AgentRuntime) -> FastAPI:
    app = FastAPI(title="A-Share Agent Workbench", version="0.6.0")
    static_dir = Path(__file__).resolve().parent / "static"
    ttl = int(runtime.config.runtime.get("web", {}).get("dashboard_refresh_seconds", 30))
    service = DashboardService(runtime, runtime.mcp, runtime.config, ttl_seconds=ttl)
    workbench = WorkbenchService(service)
    app.state.runtime = runtime
    app.state.dashboard = service
    app.state.workbench = workbench
    app.state.backtests = BacktestService(runtime.config, runtime.mcp)
    stream_cfg = runtime.config.runtime.get("web", {}).get("sse", {})
    app.state.event_stream = AuditSSEStream(
        runtime.audit.index,
        poll_seconds=float(stream_cfg.get("poll_seconds", 0.75)),
        heartbeat_seconds=float(stream_cfg.get("heartbeat_seconds", 15)),
    )

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/dashboard")
    async def dashboard(force: bool = Query(False)) -> dict[str, Any]:
        return await asyncio.to_thread(service.snapshot, force=force)

    @app.get("/api/market")
    async def market(force: bool = Query(False)) -> dict[str, Any]:
        return await asyncio.to_thread(service.market_context, force=force)

    @app.get("/api/sectors")
    async def sectors(force: bool = Query(False)) -> list[dict[str, Any]]:
        market_ctx = await asyncio.to_thread(service.market_context, force=force)
        return await asyncio.to_thread(service.hot_sectors, market_ctx, force=force)

    @app.get("/api/sector/detail")
    async def sector_detail(code: str | None = Query(None), name: str | None = Query(None), force: bool = Query(False)) -> dict[str, Any]:
        if not code and not name:
            raise HTTPException(status_code=400, detail="code or name is required")
        return await asyncio.to_thread(workbench.sector_detail, code=code, name=name, force=force)

    @app.get("/api/stock/{symbol}")
    async def stock_detail(symbol: str, date: str | None = Query(None)) -> dict[str, Any]:
        return await asyncio.to_thread(workbench.stock_detail, symbol, trade_date=date)

    @app.get("/api/limitup")
    async def limitup(force: bool = Query(False)) -> dict[str, Any]:
        return await asyncio.to_thread(service.limitup, force=force)

    @app.get("/api/candidates")
    async def candidates(date: str | None = Query(None)) -> dict[str, Any]:
        return await asyncio.to_thread(service.candidates, date)

    @app.get("/api/audit")
    async def audit(date: str | None = Query(None), limit: int = Query(80, ge=1, le=500)) -> list[dict[str, Any]]:
        return await asyncio.to_thread(service.audit_timeline, date, limit)

    @app.get("/api/audit/event/{event_id}")
    async def audit_event(event_id: str) -> dict[str, Any]:
        event = await asyncio.to_thread(workbench.event_detail, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return event

    @app.get("/api/replay/dates")
    async def replay_dates(limit: int = Query(120, ge=1, le=1000)) -> dict[str, Any]:
        return {"dates": await asyncio.to_thread(workbench.available_dates, limit)}

    @app.get("/api/replay/{trade_date}")
    async def replay_day(trade_date: str) -> dict[str, Any]:
        return await asyncio.to_thread(workbench.replay_day, trade_date)

    @app.get("/api/replay/{trade_date}/trace/{trace_id}")
    async def replay_trace(trade_date: str, trace_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(workbench.replay_trace, trade_date, trace_id)

    @app.get("/api/review")
    async def review(date: str | None = Query(None)) -> dict[str, Any] | None:
        return await asyncio.to_thread(service.latest_review, date)

    @app.get("/api/worker/status")
    async def worker_status() -> dict[str, Any]:
        status = await asyncio.to_thread(runtime.worker_state.latest_status)
        history = await asyncio.to_thread(runtime.worker_state.phase_history, as_trade_date(), 30)
        return {"status": status, "phase_history": history}

    @app.get("/api/events/stream")
    async def events_stream(request: Request, after: int | None = Query(None)) -> StreamingResponse:
        header = request.headers.get("last-event-id")
        start = after if after is not None else (int(header) if header else runtime.audit.index.max_seq())
        async def gen():
            async for chunk in app.state.event_stream.iterate(after_seq=start):
                if await request.is_disconnected():
                    break
                yield chunk
        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


    @app.get("/api/notifications/event/{event_id}")
    async def notification_event(event_id: str) -> dict[str, Any]:
        event = await asyncio.to_thread(runtime.audit.index.get_event, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        browser_cfg = runtime.config.runtime.get("notifications", {}).get("browser", {}) or {}
        enabled = bool(browser_cfg.get("enabled", True))
        allowed = set(browser_cfg.get("event_types", []))
        should = enabled and (not allowed or str(event.get("event_type")) in allowed) and runtime.notifications.formatter.should_notify(event)
        if not should:
            return {"notify": False, "event_id": event_id}
        msg = runtime.notifications.formatter.format(event)
        return {
            "notify": True,
            "event_id": event_id,
            "title": msg.title,
            "severity": msg.severity,
            "text": msg.text,
            "fields": [{"label": k, "value": v} for k, v in msg.fields],
            "event_type": msg.event_type,
            "symbol": msg.symbol,
            "trace_id": event.get("trace_id"),
            "trade_date": event.get("trade_date"),
        }

    @app.get("/api/notifications/recent")
    async def notification_recent(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await asyncio.to_thread(runtime.notifications.recent, limit)

    @app.get("/api/backtests")
    async def backtests(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return await asyncio.to_thread(app.state.backtests.list_runs, limit)

    @app.get("/api/backtests/{run_id}")
    async def backtest_detail(run_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(app.state.backtests.load_run, run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="backtest not found")
        return item

    @app.get("/api/backtests/{run_id}/file/{name}")
    async def backtest_file(run_id: str, name: str):
        allowed={"report.html","report.json","trades.csv","equity_curve.csv","monthly_returns.csv","rejections.csv","walk_forward.json"}
        if name not in allowed:
            raise HTTPException(status_code=400, detail="unsupported report file")
        p=runtime.config.project_root/"data"/"backtest"/"runs"/run_id/name
        if not p.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(p)

    @app.post("/api/backtests/run")
    async def run_backtest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        if not bool(runtime.config.runtime.get("web", {}).get("allow_backtest", True)):
            raise HTTPException(status_code=403, detail="backtest disabled by config")
        symbols=payload.pop("symbols", None) or []
        walk_forward=bool(payload.pop("walk_forward", False))
        # Research-only: this path never calls Risk/Exec tools.
        return await asyncio.to_thread(app.state.backtests.submit, payload, symbols, walk_forward=walk_forward)

    @app.get("/api/backtests/jobs/{job_id}")
    async def backtest_job(job_id: str) -> dict[str, Any]:
        item=await asyncio.to_thread(app.state.backtests.get_job, job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="job not found")
        return item

    @app.post("/api/control/run-phase/{phase}")
    async def run_phase(phase: str) -> dict[str, Any]:
        if not bool(runtime.config.runtime.get("web", {}).get("allow_phase_control", False)):
            raise HTTPException(status_code=403, detail="phase control disabled by config")
        try:
            return await asyncio.to_thread(runtime.run_phase, phase)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---- Phase 7 WebUI Deep Integration ----

    @app.get("/api/v1/dashboard")
    async def v1_dashboard(force: bool = Query(False)) -> dict[str, Any]:
        return await asyncio.to_thread(service.get_full_dashboard, force=force)

    @app.get("/api/v1/candidates")
    async def v1_candidates(
        page: int = Query(1, ge=1),
        pattern: str | None = Query(None),
        regime: str | None = Query(None),
        sector: str | None = Query(None),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            service.get_candidates, page=page, pattern_family=pattern,
            regime=regime, sector=sector,
        )

    @app.get("/api/v1/positions")
    async def v1_positions() -> dict[str, Any]:
        return await asyncio.to_thread(service.get_positions)

    @app.get("/api/v1/backtest")
    async def v1_backtest(
        pattern: str | None = Query(None),
        regime: str | None = Query(None),
        lifecycle: str | None = Query(None),
        from_date: str | None = Query(None),
        to_date: str | None = Query(None),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            service.get_backtest_results, pattern=pattern, regime=regime,
            lifecycle=lifecycle, from_date=from_date, to_date=to_date,
        )

    @app.get("/api/v1/trades/{round_trip_id}")
    async def v1_trade_detail(round_trip_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(service.get_trade_detail, round_trip_id)

    @app.get("/api/v1/strategy-lab")
    async def v1_strategy_lab(
        pattern: str | None = Query(None),
        regime: str | None = Query(None),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            service.get_strategy_lab, pattern=pattern, regime=regime,
        )

    return app
