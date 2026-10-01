from __future__ import annotations

import json
from collections import Counter
from typing import Any, Callable

from ..utils import as_trade_date, now_shanghai
from .dashboard import DashboardService, _as_list, _bars, _num, _text, _sector_trend


class WorkbenchService:
    """Read-only detail/replay service for the web workbench.

    No method here may create intents, submit/cancel orders, or call execution side effects.
    """

    def __init__(self, dashboard: DashboardService):
        self.dashboard = dashboard
        self.runtime = dashboard.runtime
        self.mcp = dashboard.mcp

    @staticmethod
    def _safe(call: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": call(), "error": None}
        except Exception as exc:
            return {"ok": False, "data": None, "error": str(exc)}

    def sector_detail(self, *, code: str | None = None, name: str | None = None, force: bool = False) -> dict[str, Any]:
        market = self.dashboard.market_context(force=force)
        sectors = self.dashboard.hot_sectors(market, force=force)
        sector = next((s for s in sectors if (code and s.get("code") == code) or (name and s.get("name") == name)), None)
        if sector is None:
            sector = {"name": name or code or "unknown", "code": code, "rank": None, "score": None,
                      "strength": "unknown", "lifecycle": "unknown", "route": {}, "leaders": []}
            if code:
                history_res = self._safe(lambda: self.mcp.invoke("mcp_intel_fetch_sector_history", code=code))
                sector["trend"] = _sector_trend(history_res["data"] if history_res["ok"] else [])
                sector["history_error"] = history_res["error"]
            else:
                sector["trend"] = _sector_trend([])

        ladder = self.dashboard.limitup(force=force)
        related_limitup = [x for x in ladder.get("stocks", []) if name and x.get("sector") == name]
        hot = self.dashboard.hot_signals(force=force)
        hot_items = hot if isinstance(hot, list) else _as_list(hot, "items", "data", "signals")
        related_hot = [x for x in hot_items if not name or _text(x, "sector", "theme", "板块") == name]

        leaders = []
        for raw in sector.get("leaders", []) or []:
            symbol = _text(raw, "symbol", "code", "证券代码")
            leaders.append({
                "symbol": symbol,
                "name": _text(raw, "name", "stock", "证券简称"),
                "quote": self._safe(lambda s=symbol: self.mcp.invoke("mcp_intel_query_data", symbol=s))["data"] if symbol else None,
            })

        return {
            "as_of": now_shanghai().isoformat(),
            "market": {
                "market_regime": market.get("market_regime"),
                "market_trend": market.get("market_trend"),
                "sentiment_phase": market.get("sentiment_phase"),
            },
            "sector": sector,
            "leaders": leaders,
            "limitup_stocks": related_limitup,
            "hot_signals": related_hot,
            "explanation": {
                "route_id": (sector.get("route") or {}).get("route_id"),
                "allowed": (sector.get("route") or {}).get("allowed", []),
                "conditional": (sector.get("route") or {}).get("conditional", []),
                "blocked": (sector.get("route") or {}).get("blocked", []),
                "position_multiplier": (sector.get("route") or {}).get("position_multiplier"),
                "threshold_delta": (sector.get("route") or {}).get("threshold_delta"),
            },
        }

    def stock_detail(self, symbol: str, *, trade_date: str | None = None) -> dict[str, Any]:
        date = trade_date or as_trade_date()
        calls = {
            "quote": self._safe(lambda: self.mcp.invoke("mcp_intel_query_data", symbol=symbol)),
            "tdx_quote": self._safe(lambda: self.mcp.invoke("mcp_intel_tdx_quotes", symbol=symbol)),
            "kline": self._safe(lambda: self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=120)),
            "technical": self._safe(lambda: self.mcp.invoke("mcp_intel_get_technical_indicators", symbol=symbol)),
            "chip": self._safe(lambda: self.mcp.invoke("mcp_intel_get_chip_distribution", symbol=symbol)),
            "fund_flow": self._safe(lambda: self.mcp.invoke("mcp_intel_get_fund_flow", symbol=symbol)),
            "f10": self._safe(lambda: self.mcp.invoke("mcp_intel_tdx_f10", symbol=symbol, module="basic")),
            "financial": self._safe(lambda: self.mcp.invoke("mcp_intel_get_financial_report", symbol=symbol, num=2)),
            "news": self._safe(lambda: self.mcp.invoke("mcp_intel_tdx_news", symbol=symbol)),
        }
        bars = _bars(calls["kline"]["data"]) if calls["kline"]["ok"] else []
        if isinstance(calls["kline"]["data"], list):
            bars = calls["kline"]["data"]
        deterministic = self.runtime.signal_engine.scan(bars) if bars else {"entry_signals": [], "exit_signals": []}

        rows = self.runtime.audit.index.query(trade_date=date, symbol=symbol, limit=1000)
        audit = [self._row_to_event(r, materialize=False) for r in rows]
        decisions = [x for x in audit if x.get("event_type") == "SIGNAL_DECISION"]
        risk = [x for x in audit if x.get("event_type") in {"RISK_INTENT", "RISK_RESULT"}]
        executions = [x for x in audit if x.get("event_type") in {"ORDER_PROPOSAL", "ORDER_REQUEST", "EXECUTION_RECEIPT"}]

        account = self.dashboard.account(force=True)
        positions = [x for x in (account.get("positions") or []) if str(x.get("symbol")) == symbol]
        trades = [x for x in (account.get("today_trades") or []) if str(x.get("symbol")) == symbol]
        orders = [x for x in (account.get("pending_orders") or []) if str(x.get("symbol")) == symbol]

        return {
            "symbol": symbol,
            "trade_date": date,
            "as_of": now_shanghai().isoformat(),
            "market_data": calls,
            "deterministic_signals": deterministic,
            "audit": {
                "event_count": len(audit),
                "events": audit,
                "signal_decisions": decisions,
                "risk_events": risk,
                "execution_events": executions,
            },
            "account": {"positions": positions, "today_trades": trades, "pending_orders": orders},
        }

    def replay_day(self, trade_date: str) -> dict[str, Any]:
        events = self.runtime.replay.load_day(trade_date)
        verify = self.runtime.replay.verify_hash_chain(trade_date)
        counts = Counter(str(e.get("event_type")) for e in events)
        symbols = sorted({str(e.get("symbol")) for e in events if e.get("symbol")})
        traces: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            tid = event.get("trace_id")
            if tid:
                traces.setdefault(str(tid), []).append(event)
        trace_summaries = []
        for tid, evs in traces.items():
            trace_summaries.append({
                "trace_id": tid,
                "first_time": min(str(e.get("event_time")) for e in evs),
                "last_time": max(str(e.get("event_time")) for e in evs),
                "event_count": len(evs),
                "symbol": next((e.get("symbol") for e in evs if e.get("symbol")), None),
                "strategy_id": next((e.get("strategy_id") for e in evs if e.get("strategy_id")), None),
                "types": [str(e.get("event_type")) for e in evs],
                "status": next((e.get("status") for e in reversed(evs) if e.get("status")), None),
            })
        trace_summaries.sort(key=lambda x: x["first_time"])
        timeline = [self._event_summary(e) for e in events]
        review = next((self.runtime.replay.materialize_payload(e) for e in reversed(events) if e.get("event_type") == "DAILY_REVIEW"), None)
        manifest = next((self.runtime.replay.materialize_payload(e) for e in reversed(events) if e.get("event_type") == "AUDIT_MANIFEST"), None)
        return {
            "trade_date": trade_date,
            "verify": verify,
            "event_count": len(events),
            "event_type_counts": dict(counts),
            "symbols": symbols,
            "traces": trace_summaries,
            "timeline": timeline,
            "daily_review": review,
            "manifest": manifest,
        }

    def replay_trace(self, trade_date: str, trace_id: str) -> dict[str, Any]:
        events = self.runtime.replay.exact(trade_date, trace_id=trace_id)
        return {
            "trade_date": trade_date,
            "trace_id": trace_id,
            "event_count": len(events),
            "events": [self._materialized_event(e) for e in events],
        }

    def event_detail(self, event_id: str) -> dict[str, Any] | None:
        row = self.runtime.audit.index.get_event(event_id)
        if not row:
            return None
        event = self._row_to_event(row, materialize=True)
        return event

    def available_dates(self, limit: int = 120) -> list[str]:
        return self.runtime.audit.index.trade_dates(limit=limit)

    def _materialized_event(self, event: dict[str, Any]) -> dict[str, Any]:
        out = dict(event)
        out["materialized_payload"] = self.runtime.replay.materialize_payload(event)
        return out

    def _row_to_event(self, row: dict[str, Any], *, materialize: bool) -> dict[str, Any]:
        payload = json.loads(row["payload_json"]) if row.get("payload_json") else None
        out = {
            "event_id": row.get("event_id"), "event_time": row.get("event_time"), "trade_date": row.get("trade_date"),
            "event_type": row.get("event_type"), "phase": row.get("phase"), "run_id": row.get("run_id"),
            "trace_id": row.get("trace_id"), "intent_id": row.get("intent_id"), "symbol": row.get("symbol"),
            "strategy_id": row.get("strategy_id"), "status": row.get("status"), "payload_ref": row.get("payload_ref"),
            "payload_sha256": row.get("payload_sha256"), "event_hash": row.get("event_hash"),
            "previous_event_hash": row.get("previous_event_hash"), "payload": payload,
        }
        if materialize and row.get("payload_ref"):
            try:
                out["materialized_payload"] = self.runtime.audit.snapshot_store.get(str(row["trade_date"]), str(row["payload_ref"]))
            except Exception as exc:
                out["materialized_payload_error"] = str(exc)
        return out

    @staticmethod
    def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"), "event_time": event.get("event_time"), "event_type": event.get("event_type"),
            "phase": event.get("phase"), "trace_id": event.get("trace_id"), "intent_id": event.get("intent_id"),
            "symbol": event.get("symbol"), "strategy_id": event.get("strategy_id"), "status": event.get("status"),
            "payload_ref": event.get("payload_ref"),
        }
