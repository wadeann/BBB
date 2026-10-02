from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Position, Trade
from .costs import AShareCostModel


@dataclass
class Portfolio:
    initial_cash: float
    cash: float | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    realized_pnl: float = 0.0

    def __post_init__(self):
        if self.cash is None:
            self.cash = float(self.initial_cash)

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.quantity * float(prices.get(s, p.entry_price)) for s, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return float(self.cash or 0) + self.market_value(prices)

    def buy(self, *, symbol: str, date: str, signal_date: str, raw_price: float, quantity: int, stop_price: float,
            strategy_id: str, strategy_family: str, score: float, route_id: str | None, sector: str | None,
            cost_model: AShareCostModel, meta: dict[str, Any] | None = None) -> Trade | None:
        if symbol in self.positions or quantity < 100:
            return None
        price = cost_model.slip_price(raw_price, "BUY")
        amount = price * quantity
        fees = cost_model.fees(amount, "BUY")
        slippage = max(0.0, (price - raw_price) * quantity)
        if amount + fees > float(self.cash or 0):
            return None
        self.cash = float(self.cash or 0) - amount - fees
        pos = Position(
            symbol=symbol, entry_date=date, entry_price=price, quantity=quantity, stop_price=stop_price,
            strategy_id=strategy_id, strategy_family=strategy_family, score=score, sector=sector,
            route_id=route_id, signal_date=signal_date, highest_price=price, lowest_price=price,
            entry_cost=fees, meta=meta or {}, raw_entry_price=raw_price, entry_slippage_cost=slippage,
        )
        self.positions[symbol] = pos
        tr = Trade(
            symbol, "BUY", signal_date, date, price, quantity, amount, fees, "ENTRY",
            strategy_id, strategy_family, score, route_id, sector,
            raw_price=raw_price, slippage_cost=slippage,
            llm_decision=str((meta or {}).get("llm_decision") or "") or None,
        )
        self.trades.append(tr)
        return tr

    def sell(self, *, symbol: str, date: str, signal_date: str, raw_price: float, reason: str,
             cost_model: AShareCostModel) -> Trade | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        price = cost_model.slip_price(raw_price, "SELL")
        amount = price * pos.quantity
        fees = cost_model.fees(amount, "SELL")
        exit_slippage = max(0.0, (raw_price - price) * pos.quantity)
        self.cash = float(self.cash or 0) + amount - fees
        pnl = (price - pos.entry_price) * pos.quantity - pos.entry_cost - fees
        pnl_pct = pnl / (pos.entry_price * pos.quantity + pos.entry_cost) if pos.entry_price > 0 else 0
        self.realized_pnl += pnl
        mfe = (pos.highest_price / pos.entry_price - 1) if pos.entry_price else 0.0
        mae = (pos.lowest_price / pos.entry_price - 1) if pos.entry_price else 0.0
        market = (pos.meta.get("market") or {}) if isinstance(pos.meta, dict) else {}
        sector_ctx = (pos.meta.get("sector") or {}) if isinstance(pos.meta, dict) else {}
        raw_entry = pos.raw_entry_price or pos.entry_price
        gross_before_costs = (raw_price - raw_entry) * pos.quantity
        round_trip_fees = pos.entry_cost + fees
        round_trip_slippage = pos.entry_slippage_cost + exit_slippage
        tr = Trade(
            symbol, "SELL", signal_date, date, price, pos.quantity, amount, fees, reason,
            pos.strategy_id, pos.strategy_family, pos.score, pos.route_id, pos.sector,
            pnl, pnl_pct, pos.holding_days, reason, pos.entry_date, pos.entry_price,
            mfe, mae, market.get("market_regime"), sector_ctx.get("sector_strength"),
            raw_price=raw_price, slippage_cost=exit_slippage,
            gross_pnl_before_costs=gross_before_costs, round_trip_fees=round_trip_fees,
            round_trip_slippage=round_trip_slippage,
            llm_decision=str(pos.meta.get("llm_decision") or "") or None,
        )
        self.trades.append(tr)
        del self.positions[symbol]
        return tr

    def size_for_risk(self, *, equity: float, price: float, stop: float, risk_per_trade: float, max_single: float,
                      max_total: float, current_market_value: float, multiplier: float, lot: int = 100) -> int:
        if price <= 0 or stop <= 0 or stop >= price:
            return 0
        rps = price - stop
        risk_budget = equity * risk_per_trade * max(0, multiplier)
        caps = [
            risk_budget / rps,
            equity * max_single * max(0, multiplier) / price,
            max(0, equity * max_total - current_market_value) / price,
            float(self.cash or 0) / price,
        ]
        qty = int(min(caps) // lot * lot)
        return max(0, qty)
