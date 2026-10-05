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



@dataclass
class CacheItem:
    expires_at: float
    value: Any


def _normalize_limitup(ladder: Any) -> dict[str, Any]:
    if not isinstance(ladder, dict):
        return {"max_streak": None, "levels": [], "stocks": []}
    ls = ladder.get("ladder_summary", ladder.get("summary", {}))
    levels = []
    if isinstance(ls, dict):
        for k, v in ls.items():
            s = str(k).replace("板", "").strip()
            if s == "首板":
                levels.append({"streak": 1, "count": int(v or 0)})
            else:
                try:
                    levels.append({"streak": int(s), "count": int(v or 0)})
                except ValueError:
                    pass
    levels.sort(key=lambda x: x["streak"], reverse=True)
    raw_ladder = ladder.get("ladder", {})
    all_stocks = []
    if isinstance(raw_ladder, dict):
        for streak_key, stock_list in raw_ladder.items():
            streak_val = 1 if streak_key == "首板" else int(streak_key) if streak_key.isdigit() else 0
            for item in (stock_list if isinstance(stock_list, list) else []):
                all_stocks.append({
                    "symbol": _text(item, "symbol", "code"),
                    "name": _text(item, "name", "stock"),
                    "streak": streak_val,
                    "sector": _text(item, "sector", "theme"),
                    "limit_time": _text(item, "limit_time", "first_limit_time", "seal_time"),
                    "blowups": int(_num(item, "break_count", "blowups") or 0),
                })
    for s in (ladder.get("yesterday_broken_leaders", []) or []):
        all_stocks.append({
            "symbol": _text(s, "symbol", "code"),
            "name": _text(s, "name", "stock"),
            "streak": int(_num(s, "streak") or 0),
            "sector": _text(s, "sector", "theme"),
            "limit_time": _text(s, "limit_time"),
            "blowups": int(_num(s, "break_count") or 0),
        })
    all_stocks = [s for s in all_stocks if s["symbol"]]
    return {"max_streak": int(ladder.get("max_height", ladder.get("max_streak", 0)) or 0),
            "levels": levels, "stocks": all_stocks}

class DashboardService:

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
            lane_list = _as_list(lanes, "data", "items", "lanes", "sectors", "top_lanes")
            if not lane_list and isinstance(lanes, list):
                lane_list = [x for x in lanes if isinstance(x, dict)]
            out: list[dict[str, Any]] = []
            for rank, lane in enumerate(lane_list, start=1):
                name = _text(lane, "theme", "sector", "name", "板块", "题材") or f"板块{rank}"
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
                raw_ld = lane.get("leader", lane.get("leaders", lane.get("stocks", lane.get("风向标", lane.get("龙头")))))
                if isinstance(raw_ld, dict) and raw_ld.get("name"):
                    leaders = [raw_ld]
                else:
                    leaders = _as_list(lane, "leader", "leaders", "stocks", "风向标", "龙头")
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
            lambda: _normalize_limitup(self.mcp.invoke("mcp_intel_get_limitup_ladder", date=None, min_streak=1)),
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
        # Build stock name map from security_master
        name_map = {}
        csv_path = self.runtime.config.project_root / "data/backtest/security_master.csv"
        if csv_path.exists():
            import csv
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                for rec in csv.DictReader(f):
                    sym = rec.get("symbol")
                    if sym:
                        name_map[sym] = rec.get("name", "")
        for s in signals:
            sym = s.get("symbol", "")
            s["name"] = name_map.get(sym, "")
        for b in batch.get("batch", []):
            sym = b.get("symbol", "")
            b["name"] = name_map.get(sym, "")
        return {"trade_date": date, "batch": batch, "signal_decisions": signals,
                "name_map": dict(list(name_map.items())[:500])}

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
