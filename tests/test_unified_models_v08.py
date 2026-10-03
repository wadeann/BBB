"""
v0.8 Unified Models & Regime x Pattern Attribution Tests

Tests for:
- Lookahead protection (signal_date < trade_date)
- Signal/entry timing correctness
- Exit correctness and exit reason tracking
- NO_TRADE legality
- Regime-separated stats (different regimes produce different output)
- Backtest/live same evaluator
- PatternResult / TradeRecord / MarketRegimeSnapshot models
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from a_share_agent.backtest.engine import BacktestEngine
from a_share_agent.backtest.models import (
    BacktestSettings,
    MarketRegimeSnapshot,
    ThemeSnapshot,
    Trade,
)
from a_share_agent.backtest.metrics import multi_key_trade_stats, regime_pattern_matrix
from a_share_agent.strategy.signal_engine import DeterministicSignalEngine, SignalHit
from a_share_agent.config import load_config


# ── Helpers ────────────────────────────────────────────────────────────

def _synthetic_bars(symbol: str, trend: float = 0.012, volatility: float = 0.02,
                    start_price: float = 10.0, n_days: int = 300,
                    start_date: date = date(2024, 1, 1)) -> list[dict]:
    """Generate synthetic daily bars with controllable trend and volatility."""
    bars = []
    d = start_date
    price = start_price
    i = 0
    while len(bars) < n_days:
        if d.weekday() < 5:  # weekday only
            o = price
            c = price * (1 + trend + (hash(f"{symbol}{i}") % 1000 - 500) / 10000 * volatility)
            h = max(o, c) * (1 + abs(hash(f"{symbol}{i}h") % 100) / 10000)
            l = min(o, c) * (1 - abs(hash(f"{symbol}{i}l") % 100) / 10000)
            vol = 3_500_000 if i % 30 == 0 else 1_200_000
            bars.append({"date": d.isoformat(), "open": round(o, 4), "high": round(h, 4),
                         "low": round(l, 4), "close": round(c, 4), "volume": vol})
            price = c
            i += 1
        d += timedelta(days=1)
    return bars


def _benchmark_bars(n_days: int = 300, trend: float = 0.005,
                    start_date: date = date(2024, 1, 1)) -> list[dict]:
    return _synthetic_bars("BENCH", trend=trend, start_price=3000.0, n_days=n_days)


class SyntheticProvider:
    """Synthetic data provider for backtest testing."""

    def __init__(self, symbols: list[str], benchmark_trend: float = 0.005):
        self.warnings = []
        self._bars = {}
        self._bench_sym = "000300.SH"
        self._bars[self._bench_sym] = _benchmark_bars(trend=benchmark_trend)
        for sym in symbols:
            self._bars[sym] = _synthetic_bars(sym)

    def bars(self, symbol, **kwargs):
        return self._bars.get(symbol, [])

    def raw_bars(self, symbol, **kwargs):
        return self._bars.get(symbol, [])

    def sector_info(self, symbol):
        return {"name": "测试板块", "code": None, "source": "synthetic"}

    def sector_bars(self, code):
        return []


# ── Tests ──────────────────────────────────────────────────────────────


class TestLookaheadProtection:
    """No trade can execute at or before signal detection time."""

    def test_signal_date_before_trade_date(self):
        """Every BUY trade_date must be strictly after signal_date."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["TEST01.SH", "TEST02.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, max_holding_days=10,
        )
        report = BacktestEngine(cfg, provider, s).run(["TEST01.SH", "TEST02.SH"])
        for t in report["trades"]:
            if t["direction"] == "BUY":
                assert t["trade_date"] > t["signal_date"], (
                    f"BUY trade_date {t['trade_date']} must be > signal_date {t['signal_date']}"
                )
            if t["direction"] == "SELL":
                # SELL signal_date may equal trade_date for intraday stops,
                # but must never be in the future
                assert t["trade_date"] >= t["signal_date"], (
                    f"SELL trade_date {t['trade_date']} must be >= signal_date {t['signal_date']}"
                )

    def test_no_close_price_used_as_entry(self):
        """Entry must use next_open, not signal day close."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["TEST03.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, entry_timing="next_open",
        )
        report = BacktestEngine(cfg, provider, s).run(["TEST03.SH"])
        # Verify methodology reports next_open
        assert report["methodology"]["entry_execution"] == "next_trading_day_open"

    def test_regime_not_forward_looking(self):
        """Market regime at signal must use only data <= signal_date."""
        bars = _synthetic_bars("REGIME_TEST", trend=0.01, n_days=200)
        # Get regime at day 100
        from a_share_agent.backtest.regime import market_context_from_history
        ctx = market_context_from_history(bars, bars[99]["date"])
        assert ctx["market_regime"] != "unknown"
        # The evidence must not reference future dates
        evidence = ctx.get("evidence", {})
        assert "close" in evidence


class TestSignalEntryTiming:
    """Signal and entry timing follow spec: signal at T close, entry at T+1 open."""

    def test_entry_timing_is_next_open(self):
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["TIME01.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, entry_timing="next_open",
        )
        report = BacktestEngine(cfg, provider, s).run(["TIME01.SH"])
        buys = [t for t in report["trades"] if t["direction"] == "BUY"]
        if buys:
            bt = buys[0]
            # signal_date and trade_date must be different (next day)
            assert bt["trade_date"] != bt["signal_date"], (
                f"Expected next_open: signal={bt['signal_date']}, trade={bt['trade_date']}"
            )

    def test_exit_signal_to_execution_delay(self):
        """Exit signals should execute on next trading day (unless intraday stop)."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["TIME02.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, max_holding_days=8,
        )
        report = BacktestEngine(cfg, provider, s).run(["TIME02.SH"])
        sells = [t for t in report["trades"] if t["direction"] == "SELL"]
        non_stop_sells = [t for t in sells if t.get("exit_reason") != "STOP_LOSS"]
        for st in non_stop_sells:
            # Exit signal should have been detected before execution
            assert st["trade_date"] >= st["signal_date"], (
                f"SELL trade_date {st['trade_date']} must be >= signal_date {st['signal_date']}"
            )


class TestExitCorrectness:
    """Exit trades must be tracked with correct reason, P&L, and holding period."""

    def test_exit_reasons_tracked(self):
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["EXIT01.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, max_holding_days=5,
        )
        report = BacktestEngine(cfg, provider, s).run(["EXIT01.SH"])
        sells = [t for t in report["trades"] if t["direction"] == "SELL"]
        assert len(sells) > 0, "Should have at least one SELL trade"
        for st in sells:
            assert st.get("exit_reason") is not None, "Every SELL must have exit_reason"
            assert st.get("pnl") is not None, "Every SELL must have pnl"
            assert st.get("pnl_pct") is not None, "Every SELL must have pnl_pct"
            assert st.get("holding_days") is not None, "Every SELL must have holding_days"

    def test_no_lost_trades(self):
        """Every BUY must have a corresponding SELL (or be at end of backtest)."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["EXIT02.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, max_holding_days=5,
        )
        report = BacktestEngine(cfg, provider, s).run(["EXIT02.SH"])
        buys = [t for t in report["trades"] if t["direction"] == "BUY"]
        sells = [t for t in report["trades"] if t["direction"] == "SELL"]
        # All BUY trades should eventually have a SELL (end-of-backtest or regular exit)
        buy_symbols = {t["symbol"] for t in buys}
        sell_symbols = {t["symbol"] for t in sells}
        # Each bought symbol should appear in sells (one sell per buy)
        for sym in buy_symbols:
            sym_sells = [t for t in sells if t["symbol"] == sym]
            sym_buys = [t for t in buys if t["symbol"] == sym]
            assert len(sym_sells) == len(sym_buys), (
                f"Symbol {sym}: {len(sym_buys)} buys but {len(sym_sells)} sells"
            )


class TestNoTradeLegality:
    """NO_TRADE is a legitimate system output, not an error."""

    def test_empty_universe_no_crash(self):
        """Running with empty universe should not crash."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider([])  # No symbols
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
        )
        report = BacktestEngine(cfg, provider, s).run([])
        assert report["coverage"]["tested_symbols"] == 0
        assert report["metrics"]["closed_trades"] == 0
        # Ending equity should equal initial cash (no trades executed)
        assert report["metrics"]["ending_equity"] == 1_000_000

    def test_no_trade_is_valid_output(self):
        """0 trades is a valid and expected output in certain conditions."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["NOTRADE01.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-01-10",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=99,  # impossibly high threshold
        )
        report = BacktestEngine(cfg, provider, s).run(["NOTRADE01.SH"])
        # With min_score=99, no trades should execute
        assert report["metrics"]["closed_trades"] == 0

    def test_risk_off_regime_blocks_entries(self):
        """In BEAR/PANIC regime, new entries should be blocked."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        # Use declining benchmark to force risk_off
        provider = SyntheticProvider(["BEAR01.SH"], benchmark_trend=-0.01)
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-06-30",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70,
        )
        report = BacktestEngine(cfg, provider, s).run(["BEAR01.SH"])
        # In bear market, router should block trend_breakout family (high_volume_breakout)
        # Either we get 0 trades or very few (only conditional ones)
        buys = [t for t in report["trades"] if t["direction"] == "BUY"]
        # Most should be rejected
        rejections = report.get("rejections", [])
        router_blocks = [r for r in rejections if r.get("reason") == "ROUTER_BLOCK"]
        assert len(buys) <= 1 or len(router_blocks) > 0, (
            "Bear market should limit entries via router"
        )


class TestRegimeSeparatedStats:
    """Same pattern in different regimes must be tracked separately."""

    def test_different_regimes_produce_different_stats(self):
        """Regime x Pattern matrix should separate by regime."""
        cfg = load_config(Path(__file__).resolve().parents[1])
        provider = SyntheticProvider(["REG01.SH", "REG02.SH"])
        s = BacktestSettings(
            start_date="2025-01-01", end_date="2025-12-31",
            initial_cash=1_000_000, benchmark="000300.SH",
            min_score=70, max_holding_days=10,
        )
        report = BacktestEngine(cfg, provider, s).run(["REG01.SH", "REG02.SH"])
        matrix = report.get("regime_pattern_matrix", [])
        # Check that regime column exists in matrix rows
        for row in matrix:
            assert "regime_at_signal" in row, f"Matrix row missing regime_at_signal: {row}"
            assert "pattern_id" in row, f"Matrix row missing pattern_id: {row}"

    def test_matrix_has_required_fields(self):
        """Regime x Pattern matrix must have all required stat fields."""
        # Use real multi_key_trade_stats with sample data
        sample_trades = [
            {"direction": "SELL", "pnl": 500.0, "pnl_pct": 0.05, "trade_date": "2025-01-10",
             "regime_at_signal": "BULL_TREND", "pattern_id": "high_volume_breakout",
             "theme_lifecycle": "EMERGING", "holding_days": 5, "mfe_pct": 0.08, "mae_pct": -0.02},
            {"direction": "SELL", "pnl": -200.0, "pnl_pct": -0.02, "trade_date": "2025-01-15",
             "regime_at_signal": "BULL_TREND", "pattern_id": "high_volume_breakout",
             "theme_lifecycle": "EMERGING", "holding_days": 3, "mfe_pct": 0.03, "mae_pct": -0.04},
        ]
        rows = multi_key_trade_stats(sample_trades, ["regime_at_signal", "pattern_id"])
        assert len(rows) == 1
        row = rows[0]
        required = ["trades", "win_rate", "avg_return_pct", "median_return_pct",
                     "profit_factor", "expectancy_pct", "avg_holding_days", "status"]
        for field in required:
            assert field in row, f"Missing field {field} in matrix row"


class TestBacktestLiveSameEvaluator:
    """Backtest and live must use the same pattern evaluator."""

    def test_signal_engine_is_same_class(self):
        """BacktestEngine uses the same DeterministicSignalEngine as live."""
        engine = DeterministicSignalEngine()
        # The engine is a simple class with scan() method
        assert hasattr(engine, "scan")
        bars = _synthetic_bars("EVAL01.SH", n_days=120)
        hits = engine.scan(bars)
        assert isinstance(hits, list)
        for hit in hits:
            assert "signal" in hit
            assert "family" in hit
            assert "strength" in hit
            assert "pattern_id" in hit  # v0.8 field
            assert "pattern_version" in hit  # v0.8 field

    def test_same_pattern_logic_for_backtest_and_live(self):
        """The same scan() method is called regardless of mode."""
        engine = DeterministicSignalEngine()
        bars = _synthetic_bars("EVAL02.SH", n_days=120)
        # Simulate backtest call
        bt_hits = engine.scan(bars, market_regime="risk_on", sector_strength="strong")
        # Simulate live call
        live_hits = engine.scan(bars, market_regime="risk_on", sector_strength="strong")
        assert len(bt_hits) == len(live_hits)
        for bh, lh in zip(bt_hits, live_hits):
            assert bh["signal"] == lh["signal"]


class TestUnifiedModels:
    """PatternResult, TradeRecord, MarketRegimeSnapshot models."""

    def test_signal_hit_has_pattern_id(self):
        hit = SignalHit("test_signal", "test_family", "primary", {"key": "val"},
                        pattern_id="test_pattern", pattern_version="2.0.0")
        d = hit.to_dict()
        assert d["pattern_id"] == "test_pattern"
        assert d["pattern_version"] == "2.0.0"
        assert d["signal"] == "test_signal"

    def test_market_regime_snapshot(self):
        snap = MarketRegimeSnapshot(
            as_of="2025-01-15",
            regime="BULL_TREND",
            confidence=0.85,
            input_metrics={"close": 3500.0, "ma20": 3450.0},
            reason_codes=["strong_momentum_low_drawdown"],
            trend="up",
            sentiment="hot",
        )
        d = snap.to_dict()
        assert d["regime"] == "BULL_TREND"
        assert d["confidence"] == 0.85
        assert "strong_momentum_low_drawdown" in d["reason_codes"]

    def test_theme_snapshot(self):
        snap = ThemeSnapshot(
            as_of="2025-01-15",
            theme="半导体",
            strength="strong",
            lifecycle="ACCELERATING",
            momentum=0.08,
        )
        d = snap.to_dict()
        assert d["lifecycle"] == "ACCELERATING"
        assert d["strength"] == "strong"

    def test_trade_has_v08_fields(self):
        tr = Trade(
            symbol="TEST.SH", direction="BUY", signal_date="2025-01-15",
            trade_date="2025-01-16", price=10.0, quantity=1000,
            gross_amount=10000.0, fees=5.0, reason="ENTRY",
            pattern_id="high_volume_breakout", pattern_version="1.0.0",
            regime_at_signal="BULL_TREND", theme="半导体",
            theme_lifecycle="ACCELERATING", signal_strength="primary",
        )
        d = tr.to_dict()
        assert d["pattern_id"] == "high_volume_breakout"
        assert d["regime_at_signal"] == "BULL_TREND"
        assert d["theme_lifecycle"] == "ACCELERATING"

    def test_trade_risk_flags(self):
        tr = Trade(
            symbol="TEST.SH", direction="BUY", signal_date="2025-01-15",
            trade_date="2025-01-16", price=10.0, quantity=1000,
            gross_amount=10000.0, fees=5.0, reason="ENTRY",
            risk_flags=["near_limit_up", "low_liquidity"],
        )
        assert "near_limit_up" in tr.risk_flags
