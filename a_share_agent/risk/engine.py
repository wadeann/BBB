from __future__ import annotations

import math
from typing import Any

from ..config import RuntimeConfig
from ..models import LocalRiskResult


def _num(d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            try: return float(d[k])
            except (TypeError, ValueError): pass
    return default


class LocalRiskEngine:
    def __init__(self, config: RuntimeConfig):
        self.cfg = config.defaults.get("risk", {})

    def assess(self, *, direction: str, symbol: str, requested_quantity: int, entry_price: float,
               stop_price: float | None, balance: dict[str, Any], positions: list[dict[str, Any]],
               blacklist: Any, route_multiplier: float = 1.0, sector: str | None = None) -> LocalRiskResult:
        direction = direction.upper()
        reasons: list[str] = []
        if self._blacklisted(symbol, blacklist): reasons.append("BLACKLISTED")
        equity = _num(balance, "total_asset", "equity", "total_assets")
        cash = _num(balance, "cash", "available_cash", "available")
        if equity <= 0: reasons.append("INVALID_EQUITY")
        if entry_price <= 0: reasons.append("INVALID_PRICE")
        if direction == "SELL":
            available = 0
            for p in positions or []:
                if str(p.get("symbol")) == symbol:
                    available = int(p.get("available_quantity", p.get("quantity", 0)) or 0)
                    break
            approved = min(max(0, requested_quantity), available)
            if approved <= 0: reasons.append("NO_AVAILABLE_POSITION")
            return LocalRiskResult("REJECT" if reasons else "PASS", reasons, requested_quantity, approved, 0.0, 0.0, approved*entry_price, {"available_quantity": available})
        if stop_price is None or stop_price <= 0 or stop_price >= entry_price:
            reasons.append("INVALID_STOP")
            rps = 0.0
        else:
            rps = entry_price - stop_price
        risk_budget = equity * float(self.cfg.get("risk_per_trade", .005)) * max(0.0, route_multiplier)
        shares_risk = math.floor(risk_budget / rps / 100) * 100 if rps > 0 else 0
        single_cap = equity * float(self.cfg.get("max_single_position", .10)) * max(0.0, route_multiplier)
        shares_single = math.floor(single_cap / entry_price / 100) * 100 if entry_price > 0 else 0
        current_mv = sum(_num(p, "market_value", "value") for p in positions or [])
        total_cap = equity * float(self.cfg.get("max_total_equity_position", .50))
        shares_total = math.floor(max(0.0, total_cap-current_mv) / entry_price / 100) * 100 if entry_price > 0 else 0
        shares_cash = math.floor(cash / entry_price / 100) * 100 if entry_price > 0 else 0
        approved = min(max(0, int(requested_quantity)), shares_risk, shares_single, shares_total, shares_cash)
        if approved < 100: reasons.append("RISK_BUDGET_TOO_SMALL")
        status = "REJECT" if reasons else "PASS"
        return LocalRiskResult(status, reasons, requested_quantity, approved, risk_budget, rps, single_cap, {
            "equity": equity, "cash": cash, "current_market_value": current_mv,
            "shares_by_risk": shares_risk, "shares_by_single_cap": shares_single,
            "shares_by_total_cap": shares_total, "shares_by_cash": shares_cash,
        })

    @staticmethod
    def _blacklisted(symbol: str, blacklist: Any) -> bool:
        if isinstance(blacklist, list):
            for x in blacklist:
                if x == symbol or (isinstance(x, dict) and str(x.get("symbol")) == symbol): return True
        if isinstance(blacklist, dict):
            vals = blacklist.get("symbols", blacklist.get("data", []))
            return LocalRiskEngine._blacklisted(symbol, vals)
        return False
