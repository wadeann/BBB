from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass
class BacktestSettings:
    start_date: str
    end_date: str
    initial_cash: float = 1_000_000.0
    warmup_bars: int = 260
    benchmark: str = "000300.SH"
    max_positions: int = 5
    max_holding_days: int = 20
    risk_per_trade: float = 0.005
    max_single_position: float = 0.10
    max_total_position: float = 0.50
    min_score: float = 75.0
    entry_timing: str = "next_open"
    exit_timing: str = "next_open"
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_tax_rate_sell: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    block_open_at_limit: bool = True
    sector_mode: str = "historical_or_neutral"  # strict | historical_or_neutral | disabled
    position_round_lot: int = 100
    decision_engine: str = "deterministic"
    universe_source: str = "file_or_mcp"
    universe_file: str = "data/backtest/universe.txt"
    max_universe: int = 0
    cache: bool = True
    include_benchmark: bool = True
    report_title: str = "A股 Agent 两年回测"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_price: float
    strategy_id: str
    strategy_family: str
    score: float
    sector: str | None = None
    route_id: str | None = None
    signal_date: str | None = None
    highest_price: float = 0.0
    lowest_price: float = 0.0
    holding_days: int = 0
    entry_cost: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    symbol: str
    direction: str
    signal_date: str
    trade_date: str
    price: float
    quantity: int
    gross_amount: float
    fees: float
    reason: str
    strategy_id: str | None = None
    strategy_family: str | None = None
    score: float | None = None
    route_id: str | None = None
    sector: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    holding_days: int | None = None
    exit_reason: str | None = None
    entry_date: str | None = None
    entry_price: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    entry_market_regime: str | None = None
    entry_sector_strength: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PendingOrder:
    symbol: str
    direction: str
    created_date: str
    execute_date: str
    reason: str
    strategy_id: str | None = None
    strategy_family: str | None = None
    score: float | None = None
    route_id: str | None = None
    sector: str | None = None
    stop_price: float | None = None
    route_multiplier: float = 1.0
    requested_quantity: int = 0
    signal_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
