from __future__ import annotations

from dataclasses import dataclass, field, asdict
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
    sector_relative_ranking: bool = True
    route_mode: str = "enabled"  # enabled | disabled
    position_round_lot: int = 100
    decision_engine: str = "deterministic"  # deterministic | deterministic_plus_llm_filter
    universe_source: str = "point_in_time_or_fallback"
    universe_file: str = "data/backtest/universe.txt"
    universe_mode: str = "prefer_point_in_time"  # strict_point_in_time | prefer_point_in_time | current_fallback | file
    max_universe: int = 0
    cache: bool = True
    include_benchmark: bool = True
    report_title: str = "A股 Agent 两年回测"
    enabled_strategies: list[str] = field(default_factory=list)
    disabled_strategies: list[str] = field(default_factory=list)
    llm_filter_enabled: bool = False
    llm_filter_top_n: int = 20
    llm_filter_batch_size: int = 10
    llm_filter_accept: list[str] = field(default_factory=lambda: ["PASS"])
    llm_filter_anonymize_symbol: bool = True
    research_tag: str = "baseline"
    max_drawdown_pct: float = 0.0  # 0=disable. 净值回撤>pct则停止开新仓
    max_sector_exposure_pct: float = 0.0  # 0=disable. 单板块仓位上限%

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketRegimeSnapshot:
    """Point-in-time market regime classification with evidence and metadata.

    Regime values per spec: BULL_TREND, BULL_VOLATILE, ROTATION, SIDEWAYS,
    BEAR, PANIC, RECOVERY. Backward-compatible mapping from legacy risk_on/risk_off/neutral.
    """
    as_of: str
    regime: str
    confidence: float = 1.0
    input_metrics: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    trend: str = "unknown"
    sentiment: str = "unknown"
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThemeSnapshot:
    """Sector/theme lifecycle snapshot at a point in time.

    Lifecycle values: EMERGING, ACCELERATING, LEADING, MATURE, DISTRIBUTING, FADING.
    """
    as_of: str
    theme: str | None = None
    strength: str = "neutral"
    rank: float | None = None
    lifecycle: str = "UNKNOWN"
    breadth: float | None = None
    leader_count: int | None = None
    turnover_share: float | None = None
    momentum: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)

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
    raw_entry_price: float = 0.0
    entry_slippage_cost: float = 0.0
    # Entry attribution must survive until the position is closed.
    pattern_id: str | None = None
    pattern_version: str | None = None
    regime_at_signal: str | None = None
    theme: str | None = None
    theme_lifecycle: str | None = None
    signal_strength: str | None = None
    round_trip_id: str | None = None
    theme_lifecycle_confidence: float | None = None
    theme_data_quality: dict[str, Any] = field(default_factory=dict)
    regime_confidence_at_signal: float | None = None
    regime_data_quality_at_signal: dict[str, Any] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)

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
    raw_price: float | None = None
    slippage_cost: float = 0.0
    gross_pnl_before_costs: float | None = None
    round_trip_fees: float | None = None
    round_trip_slippage: float | None = None
    llm_decision: str | None = None
    # Entry attribution: immutable for the whole round trip.
    pattern_id: str | None = None
    pattern_version: str | None = None
    regime_at_signal: str | None = None
    theme: str | None = None
    round_trip_id: str | None = None
    theme_lifecycle_confidence_at_signal: float | None = None
    theme_data_quality_at_signal: dict[str, Any] = field(default_factory=dict)
    theme_lifecycle: str | None = None
    signal_strength: str | None = None
    regime_confidence_at_signal: float | None = None
    regime_data_quality_at_signal: dict[str, Any] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    # Exit context is separate so it cannot overwrite entry attribution.
    regime_at_exit: str | None = None
    theme_at_exit: str | None = None
    theme_lifecycle_at_exit: str | None = None

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
    # Pattern and entry context carried through to execution.
    pattern_id: str | None = None
    pattern_version: str | None = None
    regime_at_signal: str | None = None
    theme: str | None = None
    theme_lifecycle: str | None = None
    round_trip_id: str | None = None
    theme_lifecycle_confidence: float | None = None
    theme_data_quality: dict[str, Any] = field(default_factory=dict)
    regime_confidence_at_signal: float | None = None
    regime_data_quality_at_signal: dict[str, Any] = field(default_factory=dict)
    signal_strength: str | None = None
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
