from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


class FakeMCPInvoker:
    """Deterministic fake used by the demo and tests; never touches a broker."""
    def __init__(self):
        self.orders: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.registered: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.positions: list[dict[str, Any]] = []
        self._kill_switch: bool = False
        self._daily_loss_hit: bool = False
        self._call_timestamps: list[float] = []

    def invoke(self, tool_name: str, **kwargs: Any) -> Any:
        self.calls.append((tool_name, kwargs))
        if tool_name == "mcp_intel_is_trading_day": return {"trading_day": True}
        if tool_name == "mcp_intel_trading_sessions": return {"timezone": "Asia/Shanghai", "status": "normal"}
        if tool_name in {"mcp_intel_tdx_health", "mcp_intel_market_source_health"}: return {"ok": True, "latency_ms": 20}
        if tool_name in {"mcp_exec_get_balance"}: return {"cash": 500000.0, "total_asset": 1000000.0, "market_value": 500000.0}
        if tool_name == "mcp_intel_query_data":
            symbol = kwargs.get("symbol")
            base = {"002896.SZ": 28.64, "603728.SH": 19.82, "002371.SZ": 31.16, "600732.SH": 14.28, "600000.SH": 10.0}.get(symbol, 12.34)
            return {"symbol": symbol, "price": base, "pct": 3.26, "volume": 18600000, "amount": 428000000}
        if tool_name == "mcp_exec_get_positions": return self.positions
        if tool_name == "mcp_exec_get_orders":
            status = kwargs.get("status")
            return [o for o in self.orders if not status or o.get("status") == status]
        if tool_name == "mcp_exec_get_today_trades": return list(self.trades)
        if tool_name in {"mcp_exec_get_pnl", "mcp_risk_daily_pnl"}: return {"pnl": 0.0, "pnl_pct": 0.0}
        if tool_name == "mcp_risk_get_blacklist": return []
        if tool_name in {"mcp_intel_tdx_kline", "mcp_intel_fetch_kline"}:
            base = {"000300.SH": 4000, "000852.SH": 6200, "399006.SZ": 2900}.get(kwargs.get("symbol"), 10)
            return [{"date": f"2026-09-{(i%28)+1:02d}", "close": base + i*2, "open": base+i*2-1, "high": base+i*2+3, "low": base+i*2-3, "volume": 1000000+i*1000} for i in range(30)]
        if tool_name == "mcp_intel_fetch_market_health": return {"limit_up": 75, "limit_down": 8, "blowup_rate": 0.18, "advance_decline_ratio": 1.7}
        if tool_name == "mcp_intel_get_limitup_ladder":
            return {
                "max_streak": 6,
                "levels": {"1": 52, "2": 12, "3": 6, "4": 3, "5": 2, "6": 1},
                "stocks": [
                    {"symbol":"002896.SZ","name":"示例科技A","streak":6,"sector":"机器人","limit_time":"09:42:16","seal_amount":286000000,"blowups":0},
                    {"symbol":"603728.SH","name":"示例制造B","streak":5,"sector":"机器人","limit_time":"10:03:02","seal_amount":163000000,"blowups":1},
                    {"symbol":"002371.SZ","name":"示例芯片C","streak":5,"sector":"半导体","limit_time":"09:54:31","seal_amount":141000000,"blowups":0},
                    {"symbol":"600732.SH","name":"示例软件D","streak":4,"sector":"AI应用","limit_time":"10:18:09","seal_amount":98000000,"blowups":1},
                ],
            }
        if tool_name == "mcp_intel_get_mainline_lanes":
            return [
                {"sector":"机器人","code":"BK_ROBOT","score":86,"leaders":[{"symbol":"002896.SZ","name":"示例科技A"}]},
                {"sector":"半导体","code":"BK_SEMI","score":78,"leaders":[{"symbol":"002371.SZ","name":"示例芯片C"}]},
                {"sector":"AI应用","code":"BK_AIAPP","score":72,"leaders":[{"symbol":"600732.SH","name":"示例软件D"}]},
                {"sector":"新能源","code":"BK_NEWENERGY","score":54,"leaders":[]},
            ]
        if tool_name in {"mcp_intel_wencai_search", "mcp_intel_tdx_screener", "mcp_intel_screen_stocks"}:
            return [{"symbol": "600000.SH"}, {"symbol": "000001.SZ"}, {"symbol": "300750.SZ"}]
        if tool_name == "mcp_intel_query_batch_data":
            return [{"symbol": s, "price": 10.2 + i, "pct": 1.0+i, "volume": 1000000} for i,s in enumerate(kwargs.get("symbols", []))]
        if tool_name == "mcp_intel_get_technical_indicators": return {"MA5": 10.1, "MA10": 9.9, "MA20": 9.7, "MA60": 9.2, "MACD": {"DIF": .2, "DEA": .1}}
        if tool_name == "mcp_intel_get_chip_distribution": return {"profit_ratio": .62, "avg_cost": 9.8}
        if tool_name == "mcp_intel_get_fund_flow": return {"main_net_5d": 12000000}
        if tool_name == "mcp_intel_tdx_f10": return {"industry": "银行", "basic": {"listing_days": 5000}}
        if tool_name == "mcp_intel_get_financial_report": return {"reports": []}
        if tool_name in {"mcp_intel_tdx_news", "mcp_intel_search_news"}: return []
        if tool_name == "mcp_intel_query_multi_source_quote":
            return {"ok": True, "consensus_price": 10.0, "max_deviation_pct": 0.03, "sources": 4}
        if tool_name == "mcp_intel_tdx_quotes": return {"price": 10.0, "bid1": 9.99, "ask1": 10.0}
        if tool_name == "mcp_risk_check_intent": return {"status": "PASS", "reason": "fake risk pass"}
        if tool_name == "mcp_risk_batch_check": return [{"status": "PASS"} for _ in kwargs.get("intents", [])]

        # ── Phase 8 safety checks (evaluated before exec handlers) ───────────
        if tool_name in {"mcp_exec_place_order", "mcp_exec_register_approved_intent"}:
            now = _time.time()
            self._call_timestamps = [t for t in self._call_timestamps if now - t < 60]
            self._call_timestamps.append(now)
            if len(self._call_timestamps) > 20:
                return {"status": "RATE_LIMIT_EXCEEDED", "reason": "too many requests per minute"}
            if self._kill_switch:
                return {"status": "KILL_SWITCH_ACTIVE", "reason": "kill switch is active"}
            if self._daily_loss_hit:
                if kwargs.get("direction", "").upper() == "BUY":
                    return {"status": "DAILY_LOSS_LIMIT", "reason": "daily loss limit exceeded"}
        if tool_name == "mcp_exec_cancel_order" and self._kill_switch:
            return {"status": "KILL_SWITCH_ACTIVE", "reason": "kill switch is active"}
        # ── Exec handlers ────────────────────────────────────────────────────
        if tool_name == "mcp_exec_register_approved_intent":
            self.registered[kwargs["intent_id"]] = dict(kwargs)
            return {"status": "REGISTERED", "intent_id": kwargs["intent_id"]}
        if tool_name == "mcp_exec_place_order":
            order_id = f"FAKE-{len(self.orders)+1:05d}"
            order = {"order_id": order_id, "status": "FILLED", **kwargs}
            self.orders.append(order)
            trade = {"trade_id": f"T-{len(self.trades)+1:05d}", "order_id": order_id, "status": "FILLED", "symbol": kwargs["symbol"], "direction": kwargs["direction"], "quantity": kwargs["quantity"], "price": kwargs["price"]}
            self.trades.append(trade)
            return order
        if tool_name == "mcp_exec_cancel_order":
            for o in self.orders:
                if o["order_id"] == kwargs["order_id"]:
                    o["status"] = "CANCELLED"
                    return o
            return {"status": "NOT_FOUND"}
        if tool_name == "mcp_intel_get_watchlist": return []
        if tool_name == "mcp_intel_fetch_sector_history":
            code = kwargs.get("code")
            params = {
                "BK_ROBOT": (100.0, 1.2),
                "BK_SEMI": (100.0, 0.75),
                "BK_AIAPP": (100.0, 0.45),
                "BK_NEWENERGY": (105.0, -0.18),
            }
            base, drift = params.get(code, (100.0, 0.0))
            return [{"date": f"2026-09-{(i%28)+1:02d}", "close": base + i*drift + (i%4)*0.15} for i in range(30)]
        if tool_name == "mcp_intel_fetch_hot_signals":
            return [
                {"type":"sector_heat","sector":"机器人","strength":92,"note":"连板梯队与资金共振"},
                {"type":"breakout","sector":"半导体","strength":81,"note":"板块放量突破"},
            ]
        if tool_name == "mcp_jin10_list_calendar": return []
        if tool_name == "mcp_exec_reconcile":
            return {
                "status": "OK",
                "missing_orders": [],
                "extra_orders": [],
                "matched_orders": [{"order_id": o.get("order_id"), "status": o.get("status")} for o in self.orders],
                "reconciled_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            }
        raise KeyError(f"fake MCP does not implement {tool_name}")
