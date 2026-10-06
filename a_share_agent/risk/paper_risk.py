"""Phase 6 Paper Trading Risk Checks — all fail-closed."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaperRiskResult:
    status: str  # APPROVED | REJECTED
    reason: str | None = None
    approved_quantity: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "approved_quantity": self.approved_quantity,
            "details": self.details,
        }


class PaperRiskEngine:
    """Fail-closed paper risk checks.

    Every check returns REJECT unless all conditions are met.
    Risk checks must be pure — no I/O, no external calls.
    """

    def __init__(
        self,
        *,
        initial_cash: float = 1_000_000.0,
        max_single_position_pct: float = 0.10,
        max_total_position_pct: float = 0.50,
        max_single_order_pct: float = 0.05,
        sector_concentration_pct: float = 0.30,
        slippage_bps: float = 5.0,
        commission_rate: float = 0.0003,
        commission_min: float = 5.0,
        stamp_tax_rate_sell: float = 0.0005,
        transfer_fee_rate: float = 0.00001,
    ):
        self._initial_cash = initial_cash
        self._max_single_position_pct = max_single_position_pct
        self._max_total_position_pct = max_total_position_pct
        self._max_single_order_pct = max_single_order_pct
        self._sector_concentration_pct = sector_concentration_pct
        self._slippage_bps = slippage_bps
        self._commission_rate = commission_rate
        self._commission_min = commission_min
        self._stamp_tax_rate_sell = stamp_tax_rate_sell
        self._transfer_fee_rate = transfer_fee_rate

    def _num(self, d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
        for k in keys:
            if k in d and d[k] is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    pass
        return default

    def assess(
        self,
        *,
        direction: str,
        symbol: str,
        requested_quantity: int,
        entry_price: float,
        stop_price: float | None,
        trade_date: str,
        cash: float,
        positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        blacklist: Any = None,
        sector: str | None = None,
        sector_map: dict[str, str] | None = None,
        bought_today: set[str] | None = None,
        total_equity: float | None = None,
        limit_up_pct: float | None = None,
        limit_down_pct: float | None = None,
        is_suspended: bool = False,
        is_stock: bool = True,
    ) -> PaperRiskResult:
        direction = direction.upper()
        reasons: list[str] = []
        details: dict[str, Any] = {}

        # 1. Suspended stock
        if is_suspended:
            return PaperRiskResult("REJECTED", "SUSPENDED_STOCK", 0, {})

        # 2. Blacklist
        if self._is_blacklisted(symbol, blacklist):
            return PaperRiskResult("REJECTED", "BLACKLISTED", 0, {})

        equity = total_equity if total_equity is not None and total_equity > 0 else cash + self._position_market_value(positions, entry_price)

        # 3. Limit-up buy
        if direction == "BUY" and limit_up_pct is not None and limit_up_pct <= 0:
            reasons.append("LIMIT_UP_BUY")

        # 4. Limit-down sell
        if direction == "SELL" and limit_down_pct is not None and limit_down_pct >= 0:
            reasons.append("LIMIT_DOWN_SELL")

        # 5. Existing order for symbol
        for o in open_orders or []:
            if str(o.get("symbol")) == symbol and str(o.get("status")) in ("CREATED", "SUBMITTED", "PARTIAL_FILLED"):
                reasons.append("EXISTING_ORDER")
                break

        # 6. T+1 sell restriction
        if direction == "SELL" and bought_today and symbol in bought_today:
            reasons.append("T+1_SELL_RESTRICTION")

        # 7. Insufficient cash (BUY only)
        if direction == "BUY" and cash is not None:
            order_value = entry_price * requested_quantity
            fees = self._estimate_fees(order_value, "BUY")
            total_needed = order_value + fees
            if total_needed > cash:
                reasons.append("INSUFFICIENT_CASH")
                details["cash_available"] = cash
                details["order_value"] = order_value
                details["fees"] = fees
                details["total_needed"] = total_needed

        # 8. Single stock cap
        if direction == "BUY" and equity > 0:
            current_position_value = sum(
                self._num(p, "market_value", "value", "current_price") * self._num(p, "quantity", 0)
                for p in positions or [] if str(p.get("symbol")) == symbol
            )
            new_position_value = current_position_value + entry_price * requested_quantity
            single_cap = equity * self._max_single_position_pct
            if new_position_value > single_cap:
                reasons.append("SINGLE_STOCK_CAP")
                details["current_position_value"] = current_position_value
                details["new_position_value"] = new_position_value
                details["single_cap"] = single_cap

        # 9. Total position cap
        if direction == "BUY" and equity > 0:
            current_market_value = self._position_market_value(positions, entry_price)
            new_total_value = current_market_value + entry_price * requested_quantity
            total_cap = equity * self._max_total_position_pct
            if new_total_value > total_cap:
                reasons.append("TOTAL_POSITION_CAP")
                details["current_market_value"] = current_market_value
                details["new_total_value"] = new_total_value
                details["total_cap"] = total_cap

        # 10. Single order cap
        if direction == "BUY" and equity > 0:
            order_value = entry_price * requested_quantity
            order_cap = equity * self._max_single_order_pct
            if order_value > order_cap:
                reasons.append("SINGLE_ORDER_CAP")
                details["order_value"] = order_value
                details["single_order_cap"] = order_cap

        # 11. Sector concentration
        if direction == "BUY" and sector and sector_map and equity > 0:
            # Calculate total value in this sector
            sym_sector = sector
            sector_value = 0.0
            for p in positions or []:
                p_sym = str(p.get("symbol", ""))
                p_sector = sector_map.get(p_sym, "unknown")
                if p_sector == sym_sector:
                    sector_value += self._num(p, "market_value", "value", "current_price") * self._num(p, "quantity", 0)
            new_sector_value = sector_value + entry_price * requested_quantity
            sector_cap = equity * self._sector_concentration_pct
            if new_sector_value > sector_cap:
                reasons.append("SECTOR_CONCENTRATION")
                details["current_sector_value"] = sector_value
                details["new_sector_value"] = new_sector_value
                details["sector_cap"] = sector_cap

        # 12. Combined risk cap
        if direction == "BUY" and equity > 0 and reasons:
            # If there are already multiple caps hit, check combined
            risk_budget = equity * 0.005  # risk_per_trade = 0.5%
            if stop_price is not None and stop_price > 0 and entry_price > stop_price:
                rps = entry_price - stop_price
                max_risk_shares = math.floor(risk_budget / rps / 100) * 100 if rps > 0 else 0
                if max_risk_shares < requested_quantity:
                    reasons.append("COMBINED_RISK_CAP")
                    details["risk_budget"] = risk_budget
                    details["risk_per_share"] = rps

        if reasons:
            return PaperRiskResult("REJECTED", ";".join(reasons), 0, details)

        return PaperRiskResult("APPROVED", None, requested_quantity, details)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _position_market_value(self, positions: list[dict[str, Any]], fallback_price: float) -> float:
        total = 0.0
        for p in positions or []:
            qty = int(p.get("quantity", 0))
            price = self._num(p, "current_price", "entry_price", "market_value")
            total += qty * price
        return total

    def _estimate_fees(self, amount: float, direction: str) -> float:
        commission = max(self._commission_min, amount * self._commission_rate)
        stamp = amount * self._stamp_tax_rate_sell if direction.upper() == "SELL" else 0.0
        transfer = amount * self._transfer_fee_rate
        return commission + stamp + transfer

    def slip_price(self, raw_price: float, direction: str) -> float:
        factor = self._slippage_bps / 10000.0
        return raw_price * (1 + factor if direction.upper() == "BUY" else 1 - factor)

    @staticmethod
    def _is_blacklisted(symbol: str, blacklist: Any) -> bool:
        if blacklist is None:
            return False
        if isinstance(blacklist, list):
            for x in blacklist:
                if x == symbol or (isinstance(x, dict) and str(x.get("symbol")) == symbol):
                    return True
        if isinstance(blacklist, dict):
            vals = blacklist.get("symbols", blacklist.get("data", []))
            return PaperRiskEngine._is_blacklisted(symbol, vals)
        return False
