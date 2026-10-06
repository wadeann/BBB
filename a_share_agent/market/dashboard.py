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
    if not all_stocks:
        raw_stocks = ladder.get("stocks", [])
        if isinstance(raw_stocks, list):
            all_stocks = [{
                "symbol": _text(s, "symbol", "code"),
                "name": _text(s, "name", "stock"),
                "streak": int(_num(s, "streak") or 0),
                "sector": _text(s, "sector", "theme"),
                "limit_time": _text(s, "limit_time", "first_limit_time", "seal_time"),
                "blowups": int(_num(s, "break_count", "blowups") or 0),
            } for s in raw_stocks if isinstance(s, dict) and s.get("symbol")]
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

    def get_full_dashboard(self, *, force: bool = False) -> dict[str, Any]:
        """Aggregate dashboard view with regime, risk, themes, patterns, positions, P&L, alerts."""
        snap = self.snapshot(force=force)
        market = snap.get("market", {})
        account = snap.get("account", {})
        candidates_data = snap.get("candidates", {})
        batch = candidates_data.get("batch", {})
        batch_items = batch.get("batch", batch if isinstance(batch, list) else [])
        signals = candidates_data.get("signal_decisions", [])
        all_candidates = batch_items + signals
        positions = (account.get("positions") or
                     self.mcp.invoke("mcp_exec_get_positions"))
        pnl = account.get("pnl") or self.mcp.invoke("mcp_exec_get_pnl", period="today")
        risk_pnl = account.get("risk_pnl") or self.mcp.invoke("mcp_risk_daily_pnl")
        themes = [s.get("name") for s in (snap.get("hot_sectors") or []) if s.get("name")]
        enabled = list((self.config.strategy_router or {}).get("enabled_families", []))
        alerts = (snap.get("audit_timeline") or [])[-10:]
        return {
            "as_of": snap.get("as_of"),
            "market_regime": market.get("market_regime"),
            "regime_confidence": market.get("data_quality", {}).get("state", "unknown"),
            "risk_appetite": market.get("market_gate", "reduce"),
            "themes": themes[:8],
            "enabled_patterns": enabled,
            "candidates_count": len(all_candidates),
            "positions_summary": {
                "total_positions": len(positions or []),
                "total_market_value": account.get("balance", {}).get("market_value", 0),
                "available_cash": account.get("balance", {}).get("cash", 0),
            },
            "daily_pnl": {
                "pnl": pnl.get("pnl", 0) if isinstance(pnl, dict) else 0,
                "pnl_pct": pnl.get("pnl_pct", 0) if isinstance(pnl, dict) else 0,
                "risk_pnl": risk_pnl.get("pnl", 0) if isinstance(risk_pnl, dict) else 0,
            },
            "alerts": [
                {
                    "event_time": a.get("event_time"),
                    "event_type": a.get("event_type"),
                    "symbol": a.get("symbol"),
                    "status": a.get("status"),
                }
                for a in alerts
            ],
        }

    def get_candidates(
        self,
        page: int = 1,
        pattern_family: str | None = None,
        regime: str | None = None,
        sector: str | None = None,
    ) -> dict[str, Any]:
        """Paginated candidate query with optional filters."""
        raw = self.candidates()
        batch = raw.get("batch", {})
        batch_items = batch.get("batch", batch if isinstance(batch, list) else [])
        signals = raw.get("signal_decisions", [])
        all_items = []
        seen = set()
        for s in signals:
            sym = s.get("symbol") or ""
            if sym and sym not in seen:
                seen.add(sym)
                all_items.append(s)
        for b in batch_items:
            sym = b.get("symbol") or ""
            if sym and sym not in seen:
                seen.add(sym)
                all_items.append(b)
        # Apply filters
        filtered = []
        for item in all_items:
            sym = item.get("symbol") or ""
            p = item.get("payload") or {}
            fam = item.get("strategy_id") or p.get("strategy_id") or ""
            r_item = p.get("regime", p.get("market_regime", ""))
            sec = p.get("sector", "")
            if pattern_family and pattern_family not in fam:
                continue
            if regime and regime != r_item:
                continue
            if sector and sector.lower() not in sec.lower():
                continue
            filtered.append(item)
        per_page = 40
        total = len(filtered)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        page_items = filtered[start:end]
        return {
            "trade_date": raw.get("trade_date"),
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "items": page_items,
            "filters_applied": {
                "pattern_family": pattern_family,
                "regime": regime,
                "sector": sector,
            },
        }

    def get_positions(self) -> dict[str, Any]:
        """Current positions with enriched P&L and summary."""
        raw_positions = self.mcp.invoke("mcp_exec_get_positions")
        balance = self.mcp.invoke("mcp_exec_get_balance")
        pnl_data = self.mcp.invoke("mcp_exec_get_pnl", period="today")
        positions = []
        total_pnl = 0.0
        for p in (raw_positions or []):
            pnl_val = float(p.get("pnl", p.get("unrealized_pnl", 0)) or 0)
            total_pnl += pnl_val
            cost = float(p.get("cost_price", p.get("cost", 0)) or 0)
            qty = float(p.get("quantity", p.get("volume", 0)) or 0)
            price = float(p.get("price", p.get("last_price", p.get("market_price", 0))) or 0)
            positions.append({
                "symbol": p.get("symbol", ""),
                "name": p.get("name", ""),
                "quantity": qty,
                "cost_price": round(cost, 3) if cost else 0,
                "current_price": round(price, 3) if price else 0,
                "market_value": round(qty * price, 2),
                "cost_value": round(qty * cost, 2),
                "pnl": round(pnl_val, 2),
                "pnl_pct": round((pnl_val / (qty * cost) * 100) if qty * cost else 0, 2),
                "pnl_percent": round((pnl_val / (qty * cost) * 100) if qty * cost else 0, 2),
            })
        return {
            "as_of": now_shanghai().isoformat(),
            "total_positions": len(positions),
            "total_market_value": float(balance.get("market_value", balance.get("total_asset", 0))),
            "available_cash": float(balance.get("cash", 0)),
            "gross_pnl": round(total_pnl, 2),
            "gross_pnl_pct": round(pnl_data.get("pnl_pct", 0) * 100, 2) if isinstance(pnl_data, dict) else 0,
            "positions": positions,
        }

    def get_backtest_results(
        self,
        pattern: str | None = None,
        regime: str | None = None,
        lifecycle: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """Backtest results filtered by pattern, regime, lifecycle, date range."""
        from ..backtest.service import BacktestService
        bt = BacktestService(self.config, self.mcp)
        runs = bt.list_runs(limit=200)
        filtered = []
        for r in runs:
            settings = r.get("settings") or {}
            metrics = r.get("metrics") or {}
            if pattern and pattern not in str(settings):
                continue
            if regime:
                by_regime = metrics.get("by_market_regime") or {}
                if regime not in by_regime:
                    continue
            if lifecycle and lifecycle not in str(settings):
                continue
            if from_date:
                sd = str(settings.get("start_date", ""))
                if sd and sd < from_date:
                    continue
            if to_date:
                ed = str(settings.get("end_date", ""))
                if ed and ed > to_date:
                    continue
            filtered.append(r)
        return {
            "total": len(filtered),
            "results": filtered,
        }

    def get_trade_detail(self, round_trip_id: str) -> dict[str, Any]:
        """Full trade timeline: snapshot → signal → entry → exit → P&L."""
        try:
            from ..backtest.service import BacktestService
            bt = BacktestService(self.config, self.mcp)
            report = bt.load_run(round_trip_id)
        except Exception:
            report = None
        # Also search audit for trace/round-trip events
        events = []
        rows = self.runtime.audit.index.query(limit=500)
        for r in rows:
            payload = json.loads(r.get("payload_json", "{}")) if r.get("payload_json") else {}
            pid = payload.get("intent_id", payload.get("round_trip_id", ""))
            if pid and round_trip_id in pid:
                events.append({
                    "event_time": r.get("event_time"),
                    "event_type": r.get("event_type"),
                    "phase": r.get("phase"),
                    "symbol": r.get("symbol"),
                    "strategy_id": r.get("strategy_id"),
                    "status": r.get("status"),
                    "payload": payload,
                })
            if r.get("trace_id") and r["trace_id"] == round_trip_id:
                events.append({
                    "event_time": r.get("event_time"),
                    "event_type": r.get("event_type"),
                    "phase": r.get("phase"),
                    "symbol": r.get("symbol"),
                    "strategy_id": r.get("strategy_id"),
                    "status": r.get("status"),
                    "payload": payload,
                })
        events.sort(key=lambda e: e.get("event_time", ""))
        trade = None
        if report:
            trades = report.get("trades", [])
            for t in trades:
                if t.get("round_trip_id") == round_trip_id or t.get("symbol", "").startswith(round_trip_id[:4] if len(round_trip_id) >= 4 else ""):
                    trade = t
                    break
            if not trade and trades:
                trade = trades[0]
        metrics = report.get("metrics", {}) if report else {}
        return {
            "round_trip_id": round_trip_id,
            "trade": trade,
            "events": events,
            "event_count": len(events),
            "metrics": {
                "total_return": metrics.get("total_return"),
                "sharpe": metrics.get("sharpe"),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
                "closed_trades": metrics.get("closed_trades"),
            } if metrics else None,
        }

    def get_strategy_lab(
        self,
        pattern: str | None = None,
        regime: str | None = None,
    ) -> dict[str, Any]:
        """Strategy comparison data grouped by strategy family and regime."""
        from ..backtest.service import BacktestService
        bt = BacktestService(self.config, self.mcp)
        runs = bt.list_runs(limit=200)
        # Group strategies
        strategies = {}
        for r in runs:
            settings = r.get("settings") or {}
            metrics = r.get("metrics") or {}
            fam = settings.get("strategy_family", settings.get("family", "default"))
            if pattern and pattern not in fam:
                continue
            reg = metrics.get("market_regime", metrics.get("by_market_regime", {}))
            if isinstance(reg, dict):
                reg_key = " | ".join(reg.keys()) if not regime else regime
            else:
                reg_key = str(reg) or "mixed"
            if regime and regime != reg_key:
                continue
            key = f"{fam}::{reg_key}"
            if key not in strategies:
                strategies[key] = {
                    "family": fam,
                    "regime": reg_key,
                    "metrics": [],
                    "run_count": 0,
                }
            strategies[key]["metrics"].append({
                "run_id": r.get("run_id"),
                "total_return": metrics.get("total_return"),
                "sharpe": metrics.get("sharpe"),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
                "cagr": metrics.get("cagr"),
                "profit_factor": metrics.get("profit_factor"),
                "calmar": metrics.get("calmar"),
                "closed_trades": metrics.get("closed_trades"),
            })
            strategies[key]["run_count"] += 1
        # Build comparison table (not sorted by total return per G05)
        comparison = []
        for key, s in strategies.items():
            m = s["metrics"]
            avg_return = sum((x.get("total_return") or 0) for x in m) / len(m) if m else 0
            avg_sharpe = sum((x.get("sharpe") or 0) for x in m) / len(m) if m else 0
            avg_dd = sum((x.get("max_drawdown") or 0) for x in m) / len(m) if m else 0
            avg_win = sum((x.get("win_rate") or 0) for x in m) / len(m) if m else 0
            comparison.append({
                "family": s["family"],
                "regime": s["regime"],
                "run_count": s["run_count"],
                "avg_total_return": round(avg_return, 4),
                "avg_sharpe": round(avg_sharpe, 4),
                "avg_max_drawdown": round(avg_dd, 4),
                "avg_win_rate": round(avg_win, 4),
                "runs": s["metrics"],
            })
        return {
            "comparison": comparison,
            "total_families": len(strategies),
            "total_runs": sum(s["run_count"] for s in strategies.values()),
        }
