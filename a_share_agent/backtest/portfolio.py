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
            cost_model: AShareCostModel, meta: dict[str, Any] | None = None,
            # v0.8: unified trade attribution
            pattern_id: str | None = None,
            pattern_version: str | None = None,
            regime_at_signal: str | None = None,
            theme: str | None = None,
            theme_lifecycle: str | None = None,
            signal_strength: str | None = None,
            ) -> Trade | None:
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
            # v0.8 fields
            pattern_id=pattern_id or str(strategy_id),
            pattern_version=pattern_version or "1.0.0",
            regime_at_signal=regime_at_signal,
            theme=theme,
            theme_lifecycle=theme_lifecycle,
            signal_strength=signal_strength,
        )
        self.trades.append(tr)
        return tr

    def sell(self, *, symbol: str, date: str, signal_date: str, raw_price: float, reason: str,
             cost_model: AShareCostModel,
             # v0.8: exit attribution
             exit_regime: str | None = None,
             exit_theme: str | None = None,
             exit_theme_lifecycle: str | None = None,
             ) -> Trade | None:
        pos = self.positions.get(symbol)
        if not pos: return None
        price = cost_model.slip_price(raw_price, "SELL")
        amount = price * pos.quantity
        fees = cost_model.fees(amount, "SELL")
        stamp = cost_model.stamp_tax(amount) if hasattr(cost_model, "stamp_tax") else 0.0
        total_fees = fees + stamp
        slippage = max(0.0, (raw_price - price) * pos.quantity)
        entry_cost = pos.entry_cost or 0.0
        entry_slippage = pos.entry_slippage_cost or 0.0
        gross_pnl_before_costs = (price - pos.entry_price) * pos.quantity
        round_trip_fee = entry_cost + total_fees
        round_trip_slip = entry_slippage + slippage
        pnl = gross_pnl_before_costs - round_trip_fee - round_trip_slip
        pnl_pct = pnl / (pos.entry_price * pos.quantity) if pos.entry_price and pos.quantity else 0.0
        mfe = (pos.highest_price / pos.entry_price - 1) if pos.entry_price and pos.highest_price else 0.0
        mae = (pos.lowest_price / pos.entry_price - 1) if pos.entry_price and pos.lowest_price else 0.0
        self.cash = float(self.cash or 0) + amount - fees - stamp
        self.realized_pnl += pnl
        tr = Trade(
            symbol, "SELL", signal_date, date, price, pos.quantity, amount, total_fees, reason,
            pos.strategy_id, pos.strategy_family, pos.score, pos.route_id, pos.sector,
            pnl=pnl, pnl_pct=pnl_pct, holding_days=pos.holding_days,
            exit_reason=reason, entry_date=pos.entry_date, entry_price=pos.entry_price,
            mfe_pct=mfe, mae_pct=mae,
            raw_price=raw_price, slippage_cost=slippage,
            gross_pnl_before_costs=gross_pnl_before_costs,
            round_trip_fees=round_trip_fee, round_trip_slippage=round_trip_slip,
            # v0.8: carry forward entry attribution + add exit context
            pattern_id=pos.strategy_id,
            pattern_version="1.0.0",
            regime_at_signal=exit_regime,
            theme=exit_theme,
            theme_lifecycle=exit_theme_lifecycle,
            signal_strength=None,
        )
        self.trades.append(tr)
        del self.positions[symbol]
        return tr

    def size_for_risk(self, *, equity: float, price: float, stop: float, risk_per_trade: float, max_single: float,
                      max_total: float, current_market_value: float, multiplier: float, lot: int = 100) -> int:
        if price <= 0 or stop <= 0 or stop >= price:
            return 0
        risk_per_share = abs(price - stop)
        if risk_per_share <= 0:
            return 0
        risk_capital = equity * risk_per_trade * multiplier
        raw = int(risk_capital / risk_per_share)
        max_by_single = int(equity * max_single / price)
        room = max(0, equity * max_total - current_market_value)
        max_by_total = int(room / price)
        qty = min(raw, max_by_single, max_by_total)
        qty = (qty // lot) * lot
        return max(0, qty)
