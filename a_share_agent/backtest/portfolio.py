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
            pattern_id: str | None = None,
            pattern_version: str | None = None,
            regime_at_signal: str | None = None,
            regime_confidence_at_signal: float | None = None,
            regime_data_quality_at_signal: dict[str, Any] | None = None,
            theme: str | None = None,
            theme_lifecycle: str | None = None,
            theme_lifecycle_confidence: float | None = None,
            theme_data_quality: dict[str, Any] | None = None,
            signal_strength: str | None = None,
            round_trip_id: str | None = None,
            ) -> Trade | None:
        if symbol in self.positions or quantity < 100:
            return None
        price = cost_model.slip_price(raw_price, "BUY")
        amount = price * quantity
        fees = cost_model.fees(amount, "BUY")
        slippage = max(0.0, (price - raw_price) * quantity)
        if amount + fees > float(self.cash or 0):
            return None

        resolved_pattern_id = pattern_id or str(strategy_id)
        resolved_pattern_version = pattern_version or "1.0.0"
        risk_flags = list((meta or {}).get("risk_flags") or [])
        pos = Position(
            symbol=symbol, entry_date=date, entry_price=price, quantity=quantity, stop_price=stop_price,
            strategy_id=strategy_id, strategy_family=strategy_family, score=score, sector=sector,
            route_id=route_id, signal_date=signal_date, highest_price=price, lowest_price=price,
            entry_cost=fees, meta=meta or {}, raw_entry_price=raw_price, entry_slippage_cost=slippage,
            pattern_id=resolved_pattern_id, pattern_version=resolved_pattern_version,
            regime_at_signal=regime_at_signal, theme=theme, theme_lifecycle=theme_lifecycle,
            theme_lifecycle_confidence=theme_lifecycle_confidence,
            theme_data_quality=theme_data_quality or {},
            regime_confidence_at_signal=regime_confidence_at_signal,
            regime_data_quality_at_signal=regime_data_quality_at_signal or {},
            signal_strength=signal_strength, round_trip_id=round_trip_id, risk_flags=risk_flags,
        )
        self.positions[symbol] = pos
        tr = Trade(
            symbol, "BUY", signal_date, date, price, quantity, amount, fees, "ENTRY",
            strategy_id, strategy_family, score, route_id, sector,
            raw_price=raw_price, slippage_cost=slippage,
            llm_decision=str((meta or {}).get("llm_decision") or "") or None,
            pattern_id=resolved_pattern_id,
            pattern_version=resolved_pattern_version,
            regime_at_signal=regime_at_signal,
            regime_confidence_at_signal=regime_confidence_at_signal,
            regime_data_quality_at_signal=regime_data_quality_at_signal or {},
            theme=theme,
            theme_lifecycle=theme_lifecycle,
            theme_lifecycle_confidence_at_signal=theme_lifecycle_confidence,
            theme_data_quality_at_signal=theme_data_quality or {},
            signal_strength=signal_strength,
            round_trip_id=round_trip_id,
            risk_flags=risk_flags,
        )
        self.trades.append(tr)
        return tr

    def sell(self, *, symbol: str, date: str, signal_date: str, raw_price: float, reason: str,
             cost_model: AShareCostModel,
             exit_regime: str | None = None,
             exit_theme: str | None = None,
             exit_theme_lifecycle: str | None = None,
             ) -> Trade | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None

        price = cost_model.slip_price(raw_price, "SELL")
        amount = price * pos.quantity
        fees = cost_model.fees(amount, "SELL")
        exit_slippage = max(0.0, (raw_price - price) * pos.quantity)
        entry_cost = pos.entry_cost or 0.0
        entry_slippage = pos.entry_slippage_cost or 0.0

        # The executed prices already include slippage. Do not subtract slippage a second time.
        pnl = (price - pos.entry_price) * pos.quantity - entry_cost - fees
        invested = pos.entry_price * pos.quantity + entry_cost
        pnl_pct = pnl / invested if invested > 0 else 0.0

        mfe = (pos.highest_price / pos.entry_price - 1) if pos.entry_price and pos.highest_price else 0.0
        mae = (pos.lowest_price / pos.entry_price - 1) if pos.entry_price and pos.lowest_price else 0.0
        raw_entry = pos.raw_entry_price or pos.entry_price
        gross_pnl_before_costs = (raw_price - raw_entry) * pos.quantity
        round_trip_fees = entry_cost + fees
        round_trip_slippage = entry_slippage + exit_slippage
        market = (pos.meta.get("market") or {}) if isinstance(pos.meta, dict) else {}
        sector_ctx = (pos.meta.get("sector") or {}) if isinstance(pos.meta, dict) else {}

        self.cash = float(self.cash or 0) + amount - fees
        self.realized_pnl += pnl

        tr = Trade(
            symbol, "SELL", signal_date, date, price, pos.quantity, amount, fees, reason,
            pos.strategy_id, pos.strategy_family, pos.score, pos.route_id, pos.sector,
            pnl=pnl, pnl_pct=pnl_pct, holding_days=pos.holding_days,
            exit_reason=reason, entry_date=pos.entry_date, entry_price=pos.entry_price,
            mfe_pct=mfe, mae_pct=mae,
            # Preserve legacy entry-context fields used by existing reports.
            entry_market_regime=market.get("market_regime"),
            entry_sector_strength=sector_ctx.get("sector_strength"),
            raw_price=raw_price, slippage_cost=exit_slippage,
            gross_pnl_before_costs=gross_pnl_before_costs,
            round_trip_fees=round_trip_fees, round_trip_slippage=round_trip_slippage,
            llm_decision=str((pos.meta or {}).get("llm_decision") or "") or None,
            # Preserve the entry attribution used by Regime × Pattern analysis.
            pattern_id=pos.pattern_id or pos.strategy_id,
            pattern_version=pos.pattern_version or "1.0.0",
            regime_at_signal=pos.regime_at_signal,
            regime_confidence_at_signal=pos.regime_confidence_at_signal,
            regime_data_quality_at_signal=dict(pos.regime_data_quality_at_signal),
            theme=pos.theme,
            theme_lifecycle=pos.theme_lifecycle,
            theme_lifecycle_confidence_at_signal=pos.theme_lifecycle_confidence,
            theme_data_quality_at_signal=dict(pos.theme_data_quality),
            signal_strength=pos.signal_strength,
            round_trip_id=pos.round_trip_id,
            risk_flags=list(pos.risk_flags),
            # Keep execution-time context separate from entry attribution.
            regime_at_exit=exit_regime,
            theme_at_exit=exit_theme,
            theme_lifecycle_at_exit=exit_theme_lifecycle,
        )
        self.trades.append(tr)
        del self.positions[symbol]
        return tr

    def size_for_risk(self, *, equity: float, price: float, stop: float, risk_per_trade: float, max_single: float,
                      max_total: float, current_market_value: float, multiplier: float, lot: int = 100) -> int:
        if price <= 0 or stop <= 0 or stop >= price:
            return 0
        rps = price - stop
        risk_budget = equity * risk_per_trade * max(0.0, multiplier)
        caps = [
            risk_budget / rps,
            equity * max_single * max(0.0, multiplier) / price,
            max(0.0, equity * max_total - current_market_value) / price,
            float(self.cash or 0.0) / price,
        ]
        qty = int(min(caps) // lot * lot)
        return max(0, qty)
