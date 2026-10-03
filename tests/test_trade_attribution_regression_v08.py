from __future__ import annotations

import pytest

from a_share_agent.backtest.costs import AShareCostModel
from a_share_agent.backtest.metrics import multi_key_trade_stats
from a_share_agent.backtest.portfolio import Portfolio


def _zero_fee_costs(*, slippage_bps: float = 0.0) -> AShareCostModel:
    return AShareCostModel(
        commission_rate=0.0,
        commission_min=0.0,
        stamp_tax_rate_sell=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=slippage_bps,
    )


def test_round_trip_preserves_entry_attribution_and_separates_exit_context():
    portfolio = Portfolio(100_000.0)
    costs = _zero_fee_costs()

    buy = portfolio.buy(
        symbol="600001.SH",
        date="2025-01-20",
        signal_date="2025-01-17",
        raw_price=10.0,
        quantity=1000,
        stop_price=9.5,
        strategy_id="trend_breakout",
        strategy_family="trend_breakout",
        score=88.0,
        route_id="R1",
        sector="半导体",
        cost_model=costs,
        pattern_id="high_volume_breakout",
        pattern_version="2.3.1",
        regime_at_signal="BULL_TREND",
        theme="半导体",
        theme_lifecycle="ACCELERATING",
        signal_strength="primary",
    )
    assert buy is not None

    sell = portfolio.sell(
        symbol="600001.SH",
        date="2025-02-03",
        signal_date="2025-01-31",
        raw_price=11.0,
        reason="TREND_BREAK",
        cost_model=costs,
        exit_regime="BEAR",
        exit_theme="新能源",
        exit_theme_lifecycle="FADING",
    )
    assert sell is not None

    # Regime × Pattern attribution is defined by the entry signal, not by exit state.
    assert sell.pattern_id == "high_volume_breakout"
    assert sell.pattern_version == "2.3.1"
    assert sell.regime_at_signal == "BULL_TREND"
    assert sell.theme == "半导体"
    assert sell.theme_lifecycle == "ACCELERATING"
    assert sell.signal_strength == "primary"

    # Exit context remains available, but under separate fields.
    assert sell.regime_at_exit == "BEAR"
    assert sell.theme_at_exit == "新能源"
    assert sell.theme_lifecycle_at_exit == "FADING"


def test_slippage_is_reflected_in_execution_price_but_not_double_counted_in_pnl():
    portfolio = Portfolio(100_000.0)
    costs = _zero_fee_costs(slippage_bps=100.0)  # 1% each side for a visible test

    buy = portfolio.buy(
        symbol="600002.SH",
        date="2025-01-02",
        signal_date="2024-12-31",
        raw_price=100.0,
        quantity=100,
        stop_price=95.0,
        strategy_id="test",
        strategy_family="test",
        score=80.0,
        route_id=None,
        sector=None,
        cost_model=costs,
        pattern_id="pattern_a",
    )
    assert buy is not None
    assert buy.price == pytest.approx(101.0)

    sell = portfolio.sell(
        symbol="600002.SH",
        date="2025-01-10",
        signal_date="2025-01-09",
        raw_price=110.0,
        reason="EXIT",
        cost_model=costs,
    )
    assert sell is not None
    assert sell.price == pytest.approx(108.9)
    assert sell.gross_pnl_before_costs == pytest.approx(1000.0)
    assert sell.round_trip_slippage == pytest.approx(210.0)
    # 1000 raw gross - 210 execution slippage = 790 net with zero explicit fees.
    assert sell.pnl == pytest.approx(790.0)
    assert portfolio.realized_pnl == pytest.approx(790.0)


def test_size_for_risk_respects_cash_and_route_multiplier_caps():
    portfolio = Portfolio(10_000.0)
    qty = portfolio.size_for_risk(
        equity=1_000_000.0,
        price=100.0,
        stop=90.0,
        risk_per_trade=0.02,
        max_single=0.10,
        max_total=0.50,
        current_market_value=0.0,
        multiplier=0.50,
        lot=100,
    )
    # Cash can fund only 100 shares. A larger theoretical risk allowance must not
    # produce an oversized order that is later rejected instead of resized.
    assert qty == 100


def test_multi_key_stats_uses_true_even_sample_median():
    trades = [
        {"direction": "SELL", "pnl": 100.0, "pnl_pct": 0.01, "trade_date": "2025-01-01", "regime_at_signal": "BULL_TREND"},
        {"direction": "SELL", "pnl": 300.0, "pnl_pct": 0.03, "trade_date": "2025-01-02", "regime_at_signal": "BULL_TREND"},
    ]
    row = multi_key_trade_stats(trades, ["regime_at_signal"])[0]
    assert row["median_return_pct"] == pytest.approx(0.02)
    assert row["status"] == "INSUFFICIENT_DATA"


def test_multi_key_stats_drawdown_compounds_returns_and_does_not_auto_enable():
    returns = [0.50, -0.20, -0.20]
    trades = [
        {
            "direction": "SELL",
            "pnl": r * 1000.0,
            "pnl_pct": r,
            "trade_date": f"2025-01-{i + 1:02d}",
            "regime_at_signal": "BULL_TREND",
            "pattern_id": "pattern_a",
            "theme_lifecycle": "ACCELERATING",
        }
        for i, r in enumerate(returns)
    ]
    row = multi_key_trade_stats(trades, ["regime_at_signal", "pattern_id", "theme_lifecycle"])[0]
    # Equity path: 1.0 -> 1.5 -> 1.2 -> 0.96, so drawdown from 1.5 peak is -36%.
    assert row["max_drawdown_pct"] == pytest.approx(-0.36)

    # Descriptive stats must not make an in-sample router decision by themselves.
    sufficient = trades * 4
    for i, trade in enumerate(sufficient):
        trade["trade_date"] = f"2025-02-{i + 1:02d}"
    sufficient_row = multi_key_trade_stats(
        sufficient, ["regime_at_signal", "pattern_id", "theme_lifecycle"]
    )[0]
    assert sufficient_row["trades"] == 12
    assert sufficient_row["status"] == "SUFFICIENT_DATA"
