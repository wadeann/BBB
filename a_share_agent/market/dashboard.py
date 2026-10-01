from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from statistics import mean
from typing import Any

from ..config import RuntimeConfig
from ..mcp.base import MCPInvoker
from ..strategy.router import StrategyRouter
from ..utils import as_trade_date, now_shanghai


def _as_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _num(d: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            try:
                return float(d[key])
            except (TypeError, ValueError):
                pass
    return None


def _text(d: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _bars(payload: Any) -> list[dict[str, Any]]:
    return _as_list(payload, "bars", "data", "items", "klines", "history")


def _close_values(payload: Any) -> list[float]:
    out: list[float] = []
    for bar in _bars(payload):
        v = _num(bar, "close", "c", "收盘", "price", "value")
        if v is not None:
            out.append(v)
    return out


def _pct_change(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-lookback - 1] == 0:
        return None
    return (values[-1] / values[-lookback - 1] - 1.0) * 100.0


def _sector_trend(history: Any) -> dict[str, Any]:
    closes = _close_values(history)
    if len(closes) < 2:
        return {"state": "unknown", "change_5d": None, "change_20d": None, "ma20_slope": None, "sparkline": closes[-30:]}
    c5 = _pct_change(closes, 5)
    c20 = _pct_change(closes, 20)
    slope = None
    if len(closes) >= 25:
        ma20 = mean(closes[-20:])
        prev = mean(closes[-25:-5])
        slope = ma20 - prev
    if c5 is not None and c20 is not None:
        if c5 > 0 and c20 > 0 and (slope is None or slope > 0):
            state = "up"
        elif c5 < 0 and c20 < 0 and (slope is None or slope < 0):
            state = "down"
        else:
            state = "range"
    else:
        state = "up" if closes[-1] > closes[0] else "down" if closes[-1] < closes[0] else "range"
    return {"state": state, "change_5d": c5, "change_20d": c20, "ma20_slope": slope, "sparkline": closes[-30:]}


def _sector_strength(score: float | None, trend_state: str, cfg: dict[str, Any]) -> str:
    strong_threshold = float(cfg.get("strong_threshold", 70))
    weak_threshold = float(cfg.get("weak_threshold", 45))
    if score is None:
        return "unknown"
    if score >= strong_threshold and trend_state != "down":
        return "strong"
    if score < weak_threshold or trend_state == "down":
        return "weak"
    return "neutral"


def _sector_lifecycle(score: float | None, trend: dict[str, Any], lane: dict[str, Any]) -> str:
    explicit = _text(lane, "lifecycle", "phase", "stage", "生命周期")
    if explicit:
        return explicit
    c5 = trend.get("change_5d")
    c20 = trend.get("change_20d")
    if score is None:
        return "unknown"
    if score >= 85 and c5 is not None and c5 >= 5:
        return "accelerating"
    if score >= 70 and c5 is not None and c5 > 0 and (c20 is None or c20 >= 0):
        return "emerging"
    if score >= 70 and c5 is not None and c5 < 0:
        return "cooling"
    if score >= 80:
        return "crowded"
    return "neutral"


def _normalize_limitup(ladder: Any) -> dict[str, Any]:
    if not isinstance(ladder, dict):
        return {"max_streak": None, "levels": [], "stocks": []}
    raw_levels = ladder.get("levels", {})
    levels: list[dict[str, Any]] = []
    if isinstance(raw_levels, dict):
        for k, v in raw_levels.items():
            try:
                streak = int(str(k).replace("板", ""))
            except ValueError:
                continue
            count = int(v.get("count", 0)) if isinstance(v, dict) else int(v or 0)
            levels.append({"streak": streak, "count": count})
    elif isinstance(raw_levels, list):
        for item in raw_levels:
            if isinstance(item, dict):
                streak = int(_num(item, "streak", "level", "板数") or 0)
                count = int(_num(item, "count", "数量") or 0)
                if streak:
                    levels.append({"streak": streak, "count": count})
    levels.sort(key=lambda x: x["streak"], reverse=True)

    stocks = _as_list(ladder, "stocks", "leaders", "items", "data")
    normalized: list[dict[str, Any]] = []
    for item in stocks:
        normalized.append({
            "symbol": _text(item, "symbol", "code", "证券代码"),
            "name": _text(item, "name", "stock", "证券简称"),
            "streak": int(_num(item, "streak", "height", "连板", "连板数") or 0),
            "sector": _text(item, "sector", "theme", "题材", "板块"),
            "limit_time": _text(item, "limit_time", "first_limit_time", "封板时刻"),
            "seal_amount": _num(item, "seal_amount", "seal_value", "封单金额"),
            "blowups": int(_num(item, "blowups", "open_count", "炸板次数") or 0),
        })
    normalized.sort(key=lambda x: (x["streak"], x.get("seal_amount") or 0), reverse=True)
    return {
        "max_streak": int(_num(ladder, "max_streak", "max_height", "最高板") or (levels[0]["streak"] if levels else 0)),
        "levels": levels,
        "stocks": normalized,
    }


@dataclass
class CacheItem:
    expires_at: float
    value: Any


class DashboardService:
    """Read-only aggregation layer for the web console.

    It intentionally does not submit or cancel orders. Trading side effects remain in
    ExecutionEngine so the dashboard cannot bypass phase permissions or risk checks.
    """

    def __init__(self, runtime: Any, mcp: MCPInvoker, config: RuntimeConfig, ttl_seconds: int = 30):
        self.runtime = runtime
        self.mcp = mcp
        self.config = config
        self.ttl_seconds = ttl_seconds
        self.router = StrategyRouter(config.strategy_router)
        self._cache: dict[str, CacheItem] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, builder, *, force: bool = False):
        now = time.time()
        with self._lock:
            item = self._cache.get(key)
            if item and not force and item.expires_at > now:
                return item.value
        value = builder()
        with self._lock:
            self._cache[key] = CacheItem(now + self.ttl_seconds, value)
        return value

    def market_context(self, *, force: bool = False) -> dict[str, Any]:
        return self._cached("market_context", self.runtime.orchestrator.market.build, force=force)

    def hot_sectors(self, market: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            top_n = int(self.config.runtime.get("web", {}).get("hot_sector_top_n", 8))
            lanes = self.mcp.invoke("mcp_intel_get_mainline_lanes", top_n=top_n)
            lane_list = _as_list(lanes, "data", "items", "lanes", "sectors")
            if not lane_list and isinstance(lanes, list):
                lane_list = [x for x in lanes if isinstance(x, dict)]
            out: list[dict[str, Any]] = []
            for rank, lane in enumerate(lane_list, start=1):
                name = _text(lane, "sector", "name", "板块", "题材") or f"板块{rank}"
                code = _text(lane, "code", "sector_code", "板块代码")
                score = _num(lane, "score", "total_score", "攻击分", "资金百分位")
                history: Any = []
                history_error = None
                if code:
                    try:
                        history = self.mcp.invoke("mcp_intel_fetch_sector_history", code=code)
                    except Exception as exc:  # display the missing source rather than inventing a trend
                        history_error = str(exc)
                trend = _sector_trend(history)
                strength = _sector_strength(score, trend["state"], self.config.defaults.get("sector_filter", {}))
                lifecycle = _sector_lifecycle(score, trend, lane)
                sector_context = {"sector_strength": strength, "sector_lifecycle": lifecycle}
                route = self.router.route(market_context=market, sector_context=sector_context)
                leaders = _as_list(lane, "leaders", "stocks", "风向标", "龙头")
                out.append({
                    "rank": rank,
                    "name": name,
                    "code": code,
                    "score": score,
                    "strength": strength,
                    "lifecycle": lifecycle,
                    "trend": trend,
                    "leaders": leaders,
                    "route": {
                        "route_id": route["route_id"],
                        "allowed": route["allowed_strategy_families"],
                        "conditional": route["conditional_strategy_families"],
                        "blocked": route["blocked_strategy_families"],
                        "position_multiplier": route["position_multiplier"],
                        "threshold_delta": route["candidate_threshold_delta"],
                    },
                    "history_error": history_error,
                })
            return out
        return self._cached("hot_sectors", build, force=force)

    def limitup(self, *, force: bool = False) -> dict[str, Any]:
        return self._cached(
            "limitup",
            lambda: _normalize_limitup(self.mcp.invoke("mcp_intel_get_limitup_ladder", date=as_trade_date(), min_streak=1)),
            force=force,
        )

    def hot_signals(self, *, force: bool = False) -> Any:
        return self._cached("hot_signals", lambda: self.mcp.invoke("mcp_intel_fetch_hot_signals"), force=force)

    def account(self, *, force: bool = False) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            return {
                "balance": self.mcp.invoke("mcp_exec_get_balance"),
                "positions": self.mcp.invoke("mcp_exec_get_positions"),
                "pending_orders": self.mcp.invoke("mcp_exec_get_orders", status="PENDING"),
                "today_trades": self.mcp.invoke("mcp_exec_get_today_trades"),
                "pnl": self.mcp.invoke("mcp_exec_get_pnl", period="today"),
                "risk_pnl": self.mcp.invoke("mcp_risk_daily_pnl"),
            }
        return self._cached("account", build, force=force)

    def candidates(self, trade_date: str | None = None) -> dict[str, Any]:
        date = trade_date or as_trade_date()
        row = self.runtime.audit.index.latest(date, "CANDIDATE_BATCH")
        batch = json.loads(row["payload_json"]) if row and row.get("payload_json") else {"symbols": [], "batch": []}
        signal_rows = self.runtime.audit.index.query(trade_date=date, event_type="SIGNAL_DECISION", limit=500)
        signals = []
        for r in signal_rows:
            payload = json.loads(r["payload_json"]) if r.get("payload_json") else {}
            signals.append({
                "event_id": r["event_id"], "event_time": r["event_time"], "symbol": r.get("symbol") or payload.get("symbol"),
                "strategy_id": r.get("strategy_id") or payload.get("strategy_id"), "status": r.get("status"), "payload": payload,
            })
        return {"trade_date": date, "batch": batch, "signal_decisions": signals}

    def audit_timeline(self, trade_date: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        date = trade_date or as_trade_date()
        rows = self.runtime.audit.index.query(trade_date=date, limit=max(limit * 4, 100))
        rows = rows[-limit:]
        out = []
        for r in reversed(rows):
            payload = json.loads(r["payload_json"]) if r.get("payload_json") else None
            out.append({
                "event_time": r["event_time"], "event_type": r["event_type"], "phase": r.get("phase"),
                "symbol": r.get("symbol"), "strategy_id": r.get("strategy_id"), "status": r.get("status"),
                "trace_id": r.get("trace_id"), "intent_id": r.get("intent_id"), "payload": payload,
            })
        return out

    def latest_review(self, trade_date: str | None = None) -> dict[str, Any] | None:
        date = trade_date or as_trade_date()
        row = self.runtime.audit.index.latest(date, "DAILY_REVIEW")
        return json.loads(row["payload_json"]) if row and row.get("payload_json") else None

    def system_status(self) -> dict[str, Any]:
        now = now_shanghai()
        phase = self.runtime.scheduler.phase_at(now)
        health: dict[str, Any] = {}
        for name, args in (
            ("tdx", ("mcp_intel_tdx_health", {})),
            ("market_source", ("mcp_intel_market_source_health", {})),
        ):
            try:
                health[name] = self.mcp.invoke(args[0], **args[1])
            except Exception as exc:
                health[name] = {"ok": False, "error": str(exc)}
        return {
            "now": now.isoformat(),
            "mode": self.config.mode,
            "phase": phase,
            "strategy_version": self.config.runtime.get("strategy_version"),
            "code_version": self.config.runtime.get("code_version"),
            "mcp_health": health,
            "real_execution_allowed": bool(self.config.runtime.get("safety", {}).get("allow_real_execution", False)),
        }

    def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        market = self.market_context(force=force)
        return {
            "as_of": now_shanghai().isoformat(),
            "system": self.system_status(),
            "market": market,
            "hot_sectors": self.hot_sectors(market, force=force),
            "limitup": self.limitup(force=force),
            "hot_signals": self.hot_signals(force=force),
            "account": self.account(force=force),
            "candidates": self.candidates(),
            "audit_timeline": self.audit_timeline(),
            "daily_review": self.latest_review(),
        }
