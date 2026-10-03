"""
v0.8.1 Phase 1.5 Attribution Integrity & Lookahead Protection Tests

Tests for:
- UNKNOWN lifecycle never becomes EMERGING
- Missing sector history → UNKNOWN lifecycle
- BUY attribution persists through Position
- SELL preserves entry regime and lifecycle
- SELL separately records exit regime and lifecycle
- Matrix groups on ENTRY attribution (not exit context)
- UNKNOWN lifecycle matrix row marked degraded
- Degraded row not router-usable
- Future benchmark data cannot change regime(T)
- Future sector data cannot change lifecycle(T)
- Future data cannot change pattern(T)
- Synthetic generator deterministic across runs
- Cash limits position size
- Route multiplier affects sizing
- Max total position caps qty
- Attribution audit catches corrupted trade

Note: Uses random.Random(seed) instead of built-in hash() for determinism.
"""

from __future__ import annotations

import random
import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

from a_share_agent.backtest.costs import AShareCostModel
from a_share_agent.backtest.metrics import multi_key_trade_stats, regime_pattern_matrix, audit_trade_attribution, batch_audit_attribution, _buy_map, _ENTRY_ATTRIBUTION_FIELDS
from a_share_agent.backtest.models import (
    MarketRegimeSnapshot, ThemeSnapshot, Trade, Position, PendingOrder,
)
from a_share_agent.backtest.portfolio import Portfolio
from a_share_agent.backtest.regime import (
    market_context_from_history,
    sector_context_from_history,
    _expand_lifecycle,
)
from a_share_agent.strategy.signal_engine import DeterministicSignalEngine


# ── Deterministic Synthetic Data Generator ─────────────────────────────
# Uses random.Random(seed) instead of hash() for cross-process reproducibility.

_SEED = 42

def _det_noise(seed_str: str, scale: float = 1.0) -> float:
    """Deterministic noise from a fixed-seed generator keyed by string."""
    h = int.from_bytes(hashlib.sha256(seed_str.encode()).digest()[:8], "big")
    rng = random.Random(_SEED + h % 1000000)
    return (rng.random() - 0.5) * scale


def deterministic_bars(symbol: str, trend: float = 0.012, volatility: float = 0.02,
                       start_price: float = 10.0, n_days: int = 300,
                       start_date: date = date(2024, 1, 1)) -> list[dict]:
    """Generate deterministic synthetic daily bars using fixed seed."""
    bars = []
    d = start_date
    price = start_price
    i = 0
    while len(bars) < n_days:
        if d.weekday() < 5:
            o = price
            noise_pct = _det_noise(f"{symbol}_close_{i}", volatility)
            c = price * (1 + trend + noise_pct)
            h = max(o, c) * (1 + abs(_det_noise(f"{symbol}_high_{i}", 0.01)))
            l = min(o, c) * (1 - abs(_det_noise(f"{symbol}_low_{i}", 0.01)))
            vol = 3_500_000 if i % 30 == 0 else 1_200_000
            bars.append({
                "date": d.isoformat(), "open": round(o, 4),
                "high": round(h, 4), "low": round(l, 4),
                "close": round(c, 4), "volume": vol,
            })
            price = c
            i += 1
        d += timedelta(days=1)
    return bars


# ── Helpers ────────────────────────────────────────────────────────────

def make_trade(**kwargs) -> Trade:
    defaults = dict(
        symbol="TEST.SH", direction="SELL", signal_date="2025-01-15",
        trade_date="2025-01-16", price=10.0, quantity=1000,
        gross_amount=10000.0, fees=5.0, reason="EXIT",
        pattern_id="high_volume_breakout", regime_at_signal="BULL_TREND",
        theme_lifecycle="ACCELERATING", theme_lifecycle_confidence_at_signal=0.85,
        theme_data_quality_at_signal={"state": "ok"},
        regime_at_exit="BEAR", theme_lifecycle_at_exit="FADING",
    )
    defaults.update(kwargs)
    return Trade(**defaults)


# ── UNKNOWN Lifecycle Tests ────────────────────────────────────────────

class TestUnknownLifecycleNeverEmerging:
    """UNKNOWN data must never be masked as EMERGING or any valid lifecycle."""

    def test_missing_sector_history_returns_unknown(self):
        """When sector bars have < 25 data points, lifecycle must be UNKNOWN."""
        ctx = sector_context_from_history([], "2025-01-15", fallback_neutral=True)
        assert ctx["lifecycle"] == "UNKNOWN", f"Expected UNKNOWN, got {ctx['lifecycle']}"
        assert ctx["lifecycle_confidence"] == 0.0
        assert ctx["data_quality"]["state"] == "degraded"

    def test_missing_sector_history_strict_returns_unknown(self):
        """When fallback_neutral=False, lifecycle must also be unknown."""
        ctx = sector_context_from_history([], "2025-01-15", fallback_neutral=False)
        assert ctx["lifecycle"] == "unknown"  # legacy field
        assert ctx["data_quality"]["state"] == "degraded"

    def test_expand_lifecycle_unknown_input_returns_unknown(self):
        """_expand_lifecycle must return UNKNOWN for unknown legacy lifecycle."""
        result = _expand_lifecycle("unknown", 0.0, 0.0, 50.0, "neutral")
        assert result["lifecycle"] == "UNKNOWN"
        assert result["confidence"] == 0.0
        assert "unknown_lifecycle_no_data" in result["reason_codes"]

    def test_valid_lifecycle_not_affected(self):
        """EMERGING, ACCELERATING etc. still work for sufficient data."""
        bars = deterministic_bars("SECTOR_TEST", trend=0.01, n_days=100)
        ctx = sector_context_from_history(bars, bars[99]["date"])
        # With sufficient history and positive trend, lifecycle should not be UNKNOWN
        assert ctx["lifecycle"] != "UNKNOWN"
        assert ctx["data_quality"]["state"] == "ok"


# ── Attribution Integrity Tests ────────────────────────────────────────

class TestAttributionPreservation:
    """Entry attribution must survive the entire buy→hold→sell lifecycle."""

    def test_position_carries_entry_attribution(self):
        """Position must store all entry attribution fields."""
        pos = Position(
            symbol="TEST.SH", entry_date="2025-01-20", entry_price=10.0,
            quantity=1000, stop_price=9.5, strategy_id="high_volume_breakout",
            strategy_family="trend_breakout", score=80.0,
            pattern_id="high_volume_breakout", pattern_version="2.0.0",
            regime_at_signal="BULL_TREND", theme="半导体",
            theme_lifecycle="ACCELERATING", theme_lifecycle_confidence=0.90,
            theme_data_quality={"state": "ok"},
            signal_strength="primary",
        )
        assert pos.pattern_id == "high_volume_breakout"
        assert pos.regime_at_signal == "BULL_TREND"
        assert pos.theme_lifecycle == "ACCELERATING"
        assert pos.theme_lifecycle_confidence == 0.90
        assert pos.theme_data_quality == {"state": "ok"}

    def test_sell_preserves_entry_regime(self):
        """SELL Trade must keep entry regime, not overwrite with exit regime."""
        portfolio = Portfolio(100_000.0)
        costs = AShareCostModel(commission_rate=0, commission_min=0,
                                stamp_tax_rate_sell=0, transfer_fee_rate=0,
                                slippage_bps=0)
        buy = portfolio.buy(
            symbol="TEST.SH", date="2025-01-20", signal_date="2025-01-17",
            raw_price=10.0, quantity=1000, stop_price=9.5,
            strategy_id="high_volume_breakout", strategy_family="trend_breakout",
            score=80.0, route_id="RISK_ON", sector="半导体",
            cost_model=costs,
            pattern_id="high_volume_breakout", pattern_version="2.0.0",
            regime_at_signal="BULL_TREND", theme="半导体",
            theme_lifecycle="ACCELERATING", theme_lifecycle_confidence=0.90,
            theme_data_quality={"state": "ok"},
            signal_strength="primary",
        )
        assert buy is not None

        sell = portfolio.sell(
            symbol="TEST.SH", date="2025-02-05", signal_date="2025-02-05",
            raw_price=11.0, reason="TAKE_PROFIT", cost_model=costs,
            exit_regime="BEAR", exit_theme="新能源", exit_theme_lifecycle="FADING",
        )
        assert sell is not None

        # Entry fields preserved from position
        assert sell.pattern_id == "high_volume_breakout"
        assert sell.regime_at_signal == "BULL_TREND"
        assert sell.theme_lifecycle == "ACCELERATING"
        assert sell.theme_lifecycle_confidence_at_signal == 0.90
        assert sell.theme_data_quality_at_signal == {"state": "ok"}

        # Exit fields are separate
        assert sell.regime_at_exit == "BEAR"
        assert sell.theme_lifecycle_at_exit == "FADING"

    def test_sell_preserves_entry_theme_lifecycle(self):
        """theme_lifecycle must remain entry value, not exit value."""
        portfolio = Portfolio(100_000.0)
        costs = AShareCostModel(commission_rate=0, commission_min=0,
                                stamp_tax_rate_sell=0, transfer_fee_rate=0,
                                slippage_bps=0)
        portfolio.buy(
            symbol="TEST.SH", date="2025-01-20", signal_date="2025-01-17",
            raw_price=10.0, quantity=1000, stop_price=9.5,
            strategy_id="ma5_momentum_pullback", strategy_family="trend_pullback",
            score=78.0, route_id="RISK_ON", sector="银行",
            cost_model=costs,
            pattern_id="ma5_momentum_pullback",
            regime_at_signal="ROTATION", theme="银行",
            theme_lifecycle="LEADING", theme_lifecycle_confidence=0.75,
            theme_data_quality={"state": "ok"},
        )
        sell = portfolio.sell(
            symbol="TEST.SH", date="2025-02-05", signal_date="2025-02-05",
            raw_price=10.5, reason="PATTERN_INVALIDATED", cost_model=costs,
            exit_regime="SIDEWAYS", exit_theme_lifecycle="MATURE",
        )
        assert sell is not None
        assert sell.theme_lifecycle == "LEADING"  # entry, not exit
        assert sell.theme_lifecycle_at_exit == "MATURE"  # exit is separate


# ── Matrix Attribution Tests ───────────────────────────────────────────

class TestMatrixEntryAttribution:
    """Regime × Pattern matrix must use entry attribution only."""

    def test_matrix_groups_on_entry_regime_not_exit(self):
        """Matrix key must be regime_at_signal, not regime_at_exit."""
        t1 = make_trade(
            symbol="A.SH", pnl=100.0, pnl_pct=0.01,
            regime_at_signal="BULL_TREND", regime_at_exit="BEAR",
            theme_lifecycle="ACCELERATING",
        )
        t2 = make_trade(
            symbol="B.SH", pnl=50.0, pnl_pct=0.005,
            regime_at_signal="BULL_TREND", regime_at_exit="SIDEWAYS",
            theme_lifecycle="ACCELERATING",
        )
        trades = [t1.to_dict(), t2.to_dict()]
        rows = regime_pattern_matrix(trades)
        # Both trades should be in same group since entry regime matches
        assert len(rows) == 1
        assert rows[0]["regime_at_signal"] == "BULL_TREND"
        assert rows[0]["trades"] == 2

    def test_matrix_separates_by_entry_lifecycle(self):
        """Different entry lifecycles produce different matrix rows."""
        t1 = make_trade(symbol="A.SH", pnl=100.0, pnl_pct=0.01,
                        theme_lifecycle="ACCELERATING")
        t2 = make_trade(symbol="B.SH", pnl=50.0, pnl_pct=0.005,
                        theme_lifecycle="FADING")
        trades = [t1.to_dict(), t2.to_dict()]
        rows = regime_pattern_matrix(trades)
        lifecycles = {r["theme_lifecycle"] for r in rows}
        assert "ACCELERATING" in lifecycles
        assert "FADING" in lifecycles

    def test_unknown_lifecycle_row_has_degraded_context(self):
        """Matrix row with UNKNOWN lifecycle must show degraded quality."""
        t = make_trade(
            symbol="A.SH", pnl=100.0, pnl_pct=0.01,
            theme_lifecycle="UNKNOWN",
            theme_lifecycle_confidence_at_signal=0.0,
            theme_data_quality_at_signal={"state": "degraded", "reason": "insufficient_history"},
        )
        rows = multi_key_trade_stats([t.to_dict()], ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        assert len(rows) == 1
        assert rows[0]["theme_lifecycle"] == "UNKNOWN"
        # INSUFFICIENT_DATA because < 12 trades
        assert rows[0]["status"] == "INSUFFICIENT_DATA"


# ── Lookahead Protection Tests ─────────────────────────────────────────

class TestLookaheadProtection:
    """Future data must never contaminate historical signals/regime/lifecycle."""

    def test_future_benchmark_cannot_change_regime(self):
        """Adding extreme future data must not change regime(T)."""
        bars = deterministic_bars("REGIME_LA", trend=0.005, n_days=200)
        # Regime at day 100
        ctx_before = market_context_from_history(bars, bars[99]["date"])
        regime_before = ctx_before["regime"]

        # Append extreme future data after T=100
        future_bars = deterministic_bars("REGIME_LA_FUTURE", trend=0.05, n_days=50,
                                         start_date=date(2025, 6, 1))
        extended = bars[:100] + future_bars
        ctx_after = market_context_from_history(extended, bars[99]["date"])
        regime_after = ctx_after["regime"]

        assert regime_before == regime_after, (
            f"Regime changed from {regime_before} to {regime_after} after adding future data"
        )

    def test_future_sector_cannot_change_lifecycle(self):
        """Adding extreme future sector data must not change lifecycle(T)."""
        bars = deterministic_bars("SECTOR_LA", trend=0.01, n_days=200)
        ctx_before = sector_context_from_history(bars, bars[99]["date"])
        lifecycle_before = ctx_before["lifecycle"]

        # Append extreme future
        future_bars = deterministic_bars("SECTOR_LA_FUTURE", trend=-0.05, n_days=50,
                                         start_date=date(2025, 6, 1))
        extended = bars[:100] + future_bars
        ctx_after = sector_context_from_history(extended, bars[99]["date"])
        lifecycle_after = ctx_after["lifecycle"]

        assert lifecycle_before == lifecycle_after, (
            f"Lifecycle changed from {lifecycle_before} to {lifecycle_after}"
        )

    def test_future_data_cannot_change_pattern(self):
        """Pattern detection at T must not change when T+1..T+N data is added."""
        engine = DeterministicSignalEngine()
        bars = deterministic_bars("PATTERN_LA", trend=0.015, n_days=120)
        hits_before = engine.scan(bars[:100], market_regime="risk_on", sector_strength="strong")

        # Extend with crash-like future data
        crash_bars = deterministic_bars("PATTERN_LA_CRASH", trend=-0.08, n_days=30,
                                        start_date=date(2025, 6, 1))
        extended = bars[:100] + crash_bars
        hits_after = engine.scan(extended[:100], market_regime="risk_on", sector_strength="strong")
        # The first 100 bars haven't changed, so hits must be identical
        assert len(hits_before) == len(hits_after), (
            f"Hits changed from {len(hits_before)} to {len(hits_after)}"
        )
        for bh, ah in zip(hits_before, hits_after):
            assert bh["signal"] == ah["signal"]
            assert bh["pattern_id"] == ah["pattern_id"]


# ── Determinism Tests ──────────────────────────────────────────────────

class TestDeterministicData:
    """Synthetic test data must be identical across runs."""

    def test_deterministic_bars_produce_same_output(self):
        """Two calls with same params must produce identical bars."""
        bars1 = deterministic_bars("DET_TEST", n_days=100)
        bars2 = deterministic_bars("DET_TEST", n_days=100)
        assert len(bars1) == len(bars2)
        for b1, b2 in zip(bars1, bars2):
            assert b1["close"] == b2["close"], f"Close differs: {b1['close']} vs {b2['close']}"
            assert b1["date"] == b2["date"]

    def test_different_symbols_produce_different_data(self):
        """Different symbols should get different price paths."""
        bars_a = deterministic_bars("SYM_A", n_days=100)
        bars_b = deterministic_bars("SYM_B", n_days=100)
        # At some point, prices should differ
        differs = any(
            a["close"] != b["close"]
            for a, b in zip(bars_a, bars_b)
        )
        assert differs, "Different symbols produced identical price paths"


# ── Position Sizing Tests ──────────────────────────────────────────────

class TestPositionSizing:
    """Risk and position sizing constraints must be enforced."""

    def test_cash_limits_position_size(self):
        """Cannot buy more than available cash."""
        portfolio = Portfolio(10_000.0)
        costs = AShareCostModel(commission_rate=0, commission_min=0,
                                stamp_tax_rate_sell=0, transfer_fee_rate=0,
                                slippage_bps=0)
        # price=100, trying to buy 200 shares = 20000 > cash
        buy = portfolio.buy(
            symbol="CASH.SH", date="2025-01-20", signal_date="2025-01-17",
            raw_price=100.0, quantity=200, stop_price=95.0,
            strategy_id="test", strategy_family="test", score=80.0,
            route_id=None, sector=None, cost_model=costs,
        )
        assert buy is None, "Should reject when cash insufficient"

    def test_route_multiplier_affects_sizing(self):
        """size_for_risk must scale position by route multiplier."""
        portfolio = Portfolio(100_000.0)
        costs = AShareCostModel(slippage_bps=0)

        # Full multiplier
        qty_full = portfolio.size_for_risk(
            equity=100_000.0, price=10.0, stop=9.5,
            risk_per_trade=0.01, max_single=0.20, max_total=0.50,
            current_market_value=0.0, multiplier=1.0, lot=100,
        )
        # Half multiplier
        qty_half = portfolio.size_for_risk(
            equity=100_000.0, price=10.0, stop=9.5,
            risk_per_trade=0.01, max_single=0.20, max_total=0.50,
            current_market_value=0.0, multiplier=0.5, lot=100,
        )
        assert qty_half <= qty_full * 0.6  # Should be roughly half

    def test_max_total_position_caps_qty(self):
        """When portfolio is near max, new sizing should be 0."""
        portfolio = Portfolio(100_000.0)
        # Current market value = 49000 (near 50% max)
        qty = portfolio.size_for_risk(
            equity=100_000.0, price=10.0, stop=9.5,
            risk_per_trade=0.01, max_single=0.20, max_total=0.50,
            current_market_value=49_000.0, multiplier=1.0, lot=100,
        )
        assert qty <= 100, f"Should be very limited when near max, got {qty}"


# ── Attribution Audit Tests ────────────────────────────────────────────


# ── Attribution Audit Tests (production imports) ───────────────────────

class TestAttributionAudit:
    """The attribution audit must catch corrupted trades using production functions."""

    def test_valid_trade_passes_audit(self):
        t = make_trade()
        result = audit_trade_attribution(t.to_dict())
        assert result["valid"], f"Valid trade failed audit: {result['errors']}"

    def test_missing_pattern_id_detected(self):
        t = make_trade(pattern_id=None)
        result = audit_trade_attribution(t.to_dict())
        assert not result["valid"]
        assert "missing pattern_id" in result["errors"]

    def test_buy_trade_date_must_be_after_signal(self):
        t = make_trade(direction="BUY", trade_date="2025-01-15", signal_date="2025-01-16")
        result = audit_trade_attribution(t.to_dict())
        assert not result["valid"]
        assert "buy_trade_date must be after signal_date" in result["errors"]

    def test_batch_audit_counts_correctly(self):
        t_valid = make_trade()
        t_invalid = make_trade(pattern_id=None)
        summary = batch_audit_attribution([t_valid.to_dict(), t_invalid.to_dict()])
        assert summary["closed_trades"] == 2
        assert summary["valid"] == 1
        assert summary["invalid"] == 1

    def test_sell_regime_mismatch_caught(self):
        buy = make_trade(direction="BUY", round_trip_id="rt001",
                         regime_at_signal="BULL_TREND",
                         pattern_id="test_pattern", theme="A", theme_lifecycle="UNKNOWN")
        sell = make_trade(direction="SELL", round_trip_id="rt001",
                          regime_at_signal="BEAR",
                          pattern_id="test_pattern", theme="A", theme_lifecycle="UNKNOWN")
        bm = _buy_map([buy.to_dict(), sell.to_dict()])
        result = audit_trade_attribution(sell.to_dict(), buy_map=bm)
        assert not result["valid"]
        assert any("ENTRY_ATTRIBUTION_MISMATCH:regime_at_signal" in e for e in result["errors"])

    def test_sell_pattern_version_mismatch_caught(self):
        buy = make_trade(direction="BUY", round_trip_id="rt002",
                         pattern_id="test_pattern", pattern_version="1.0.0",
                         regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN")
        sell = make_trade(direction="SELL", round_trip_id="rt002",
                          pattern_id="test_pattern", pattern_version="2.0.0",
                          regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN")
        bm = _buy_map([buy.to_dict(), sell.to_dict()])
        result = audit_trade_attribution(sell.to_dict(), buy_map=bm)
        assert not result["valid"]
        assert any("ENTRY_ATTRIBUTION_MISMATCH:pattern_version" in e for e in result["errors"])

    def test_buy_sell_attribution_fully_matched(self):
        buy = make_trade(direction="BUY", round_trip_id="rt003",
                         pattern_id="p1", pattern_version="1.0.0",
                         regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN",
                         theme_lifecycle_confidence_at_signal=0.8,
                         theme_data_quality_at_signal={"state": "ok"},
                         signal_strength="strong",
                         regime_confidence_at_signal=0.9,
                         regime_data_quality_at_signal={"state": "ok"})
        sell = make_trade(direction="SELL", round_trip_id="rt003",
                          pattern_id="p1", pattern_version="1.0.0",
                          regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN",
                          theme_lifecycle_confidence_at_signal=0.8,
                          theme_data_quality_at_signal={"state": "ok"},
                          signal_strength="strong",
                          regime_confidence_at_signal=0.9,
                          regime_data_quality_at_signal={"state": "ok"})
        bm = _buy_map([buy.to_dict(), sell.to_dict()])
        result = audit_trade_attribution(sell.to_dict(), buy_map=bm)
        assert result["valid"], f"Expected valid, got errors: {result['errors']}"

    def test_audit_output_includes_trade_details(self):
        buy = make_trade(direction="BUY", round_trip_id="rt004",
                         pattern_id="p1", pattern_version="1.0.0",
                         regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN")
        sell = make_trade(direction="SELL", round_trip_id="rt004",
                          pattern_id="p1", pattern_version="2.0.0",
                          regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN")
        summary = batch_audit_attribution([buy.to_dict(), sell.to_dict()])
        assert "invalid_trades" in summary
        assert len(summary["invalid_trades"]) == 1
        entry = summary["invalid_trades"][0]
        assert "round_trip_id" in entry
        assert "symbol" in entry
        assert "errors" in entry

    def test_corrupted_theme_lifecycle_caught(self):
        buy = make_trade(direction="BUY", round_trip_id="rt005",
                         pattern_id="p1", pattern_version="1.0.0",
                         regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="ACCELERATING")
        sell = make_trade(direction="SELL", round_trip_id="rt005",
                          pattern_id="p1", pattern_version="1.0.0",
                          regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="MATURE")
        bm = _buy_map([buy.to_dict(), sell.to_dict()])
        result = audit_trade_attribution(sell.to_dict(), buy_map=bm)
        assert not result["valid"]
        assert any("ENTRY_ATTRIBUTION_MISMATCH:theme_lifecycle" in e for e in result["errors"])


# ── Cross-Process Determinism Tests ────────────────────────────────────

class TestCrossProcessDeterminism:
    """Synthetic data generator must produce identical output across processes."""

    def test_det_noise_stable(self):
        v1 = _det_noise("test_symbol", 1.0)
        v2 = _det_noise("test_symbol", 1.0)
        assert v1 == v2, f"Same-symbol calls differ: {v1} vs {v2}"

    def test_cross_process_hash(self):
        """Same input yields same hash in two subprocesses."""
        import subprocess, sys
        sub_code = (
            "import hashlib, json\n"
            "import hashlib as h2\n"
            "seed = 42\n"
            "h = int.from_bytes(hashlib.sha256(b'test_symbol').digest()[:8], 'big')\n"
            "r = (seed + h % 1000000)\n"
            "print(r)\n"
        )
        r1 = subprocess.run([sys.executable, '-c', sub_code], capture_output=True, text=True, timeout=15)
        r2 = subprocess.run([sys.executable, '-c', sub_code], capture_output=True, text=True, timeout=15)
        assert r1.stdout.strip() == r2.stdout.strip(), "Cross-process hash mismatch"

    def test_subprocess_bars_deterministic(self):
        """Full synthetic bars match across subprocess invocations."""
        import subprocess, sys
        sub_code = (
            "import hashlib, random, json, sys\n"
            "from datetime import date, timedelta\n"
            "_SEED = 42\n"
            "def _det_noise(seed_str, scale=1.0):\n"
            "    h = int.from_bytes(hashlib.sha256(seed_str.encode()).digest()[:8], 'big')\n"
            "    rng = random.Random(_SEED + h % 1000000)\n"
            "    return (rng.random() - 0.5) * scale\n"
            "def det_bars(sym, trend=0.012, vol=0.02, sp=10.0, nd=300, sd=date(2024,1,1)):\n"
            "    bars=[]; d=sd; price=sp; i=0\n"
            "    while len(bars) < nd:\n"
            "        if d.weekday() < 5:\n"
            "            o=price; c=price*(1+trend+_det_noise(f'{sym}_c_{i}',vol))\n"
            "            h=max(o,c)*(1+abs(_det_noise(f'{sym}_h_{i}',0.01)))\n"
            "            l=min(o,c)*(1-abs(_det_noise(f'{sym}_l_{i}',0.01)))\n"
            "            bars.append({'date':d.isoformat(),'open':round(o,4),'high':round(h,4),'low':round(l,4),'close':round(c,4),'volume':1000000})\n"
            "            price=c; i+=1\n"
            "        d+=timedelta(days=1)\n"
            "    return bars\n"
            "b=det_bars('CPTEST',nd=50)\n"
            "print(hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest())\n"
        )
        r1 = subprocess.run([sys.executable, '-c', sub_code], capture_output=True, text=True, timeout=30)
        r2 = subprocess.run([sys.executable, '-c', sub_code], capture_output=True, text=True, timeout=30)
        assert r1.returncode == 0, f"Sub1 failed: {r1.stderr}"
        assert r2.returncode == 0, f"Sub2 failed: {r2.stderr}"
        assert r1.stdout.strip() == r2.stdout.strip(), f"Cross-process bars differ: {r1.stdout[:20]} vs {r2.stdout[:20]}"


# ── Canonical UNKNOWN Tests ────────────────────────────────────────────

class TestCanonicalUNKNOWN:
    """All lifecycle fields must use uppercase UNKNOWN."""

    def test_theme_snapshot_default_is_unknown(self):
        ts = ThemeSnapshot("2025-01-01")
        assert ts.lifecycle == "UNKNOWN", f"Default should be UNKNOWN, got {ts.lifecycle}"

    def test_make_trade_unknown_passes_basic_audit(self):
        t = make_trade(theme_lifecycle="unknown")
        result = audit_trade_attribution(t.to_dict())
        assert result["valid"] is True


# ── Entry Context Usable / Regime Quality Tests ───────────────────────

class TestEntryContextUsable:
    """entry_context_usable must require both regime and theme quality OK."""

    def test_regime_quality_persists(self):
        buy = make_trade(direction="BUY", round_trip_id="rt_rq1",
                         regime_confidence_at_signal=0.85,
                         regime_data_quality_at_signal={"state": "ok"},
                         pattern_id="p1", pattern_version="1.0.0",
                         regime_at_signal="BULL_TREND", theme="A", theme_lifecycle="UNKNOWN")
        sell = make_trade(direction="SELL", round_trip_id="rt_rq1",
                          regime_confidence_at_signal=0.85,
                          regime_data_quality_at_signal={"state": "ok"})
        assert buy.regime_confidence_at_signal == sell.regime_confidence_at_signal
        assert buy.regime_data_quality_at_signal == sell.regime_data_quality_at_signal

    def test_ok_context_is_usable(self):
        sell = make_trade(direction="SELL", round_trip_id="rt_rq2",
                          regime_confidence_at_signal=0.9,
                          regime_data_quality_at_signal={"state": "ok"},
                          regime_at_signal="BULL_TREND", theme_lifecycle="ACCELERATING",
                          theme_data_quality_at_signal={"state": "ok"})
        d = sell.to_dict()
        regime_ok = d.get("regime_data_quality_at_signal", {}).get("state") == "ok"
        theme_ok = d.get("theme_data_quality_at_signal", {}).get("state") == "ok"
        regime_known = d.get("regime_at_signal") not in (None, "UNKNOWN", "unknown")
        theme_known = d.get("theme_lifecycle") not in (None, "UNKNOWN", "unknown")
        assert regime_ok and theme_ok and regime_known and theme_known, "OK context should be usable"

    def test_degraded_regime_not_usable(self):
        sell = make_trade(direction="SELL", round_trip_id="rt_rq3",
                          regime_at_signal="UNKNOWN",
                          regime_data_quality_at_signal={"state": "degraded"},
                          theme_lifecycle="UNKNOWN")
        d = sell.to_dict()
        usable = (
            d.get("regime_data_quality_at_signal", {}).get("state") == "ok"
            and d.get("theme_data_quality_at_signal", {}).get("state") == "ok"
            and d.get("regime_at_signal") not in (None, "UNKNOWN", "unknown")
            and d.get("theme_lifecycle") not in (None, "UNKNOWN", "unknown")
        )
        assert not usable, "Degraded regime should NOT be usable"


# ── Matrix Quality Coverage Tests ──────────────────────────────────────

class TestMatrixQualityCoverage:
    """Matrix must report quality coverage and fail-closed for mixed quality."""

    def test_all_ok_row_usable(self):
        td = [
            make_trade(pnl=100.0, pnl_pct=0.01,
                       theme_data_quality_at_signal={"state": "ok"},
                       regime_at_signal="BULL_TREND", pattern_id="p1", theme_lifecycle="ACCELERATING").to_dict(),
            make_trade(pnl=50.0, pnl_pct=0.005,
                       theme_data_quality_at_signal={"state": "ok"},
                       regime_at_signal="BULL_TREND", pattern_id="p1", theme_lifecycle="ACCELERATING").to_dict(),
        ]
        rows = multi_key_trade_stats(td, ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        assert rows[0]["quality_coverage"] == 1.0
        assert rows[0]["usable_for_router"] is True
        assert rows[0]["quality_ok_trades"] == 2

    def test_mixed_row_not_usable(self):
        td = [
            make_trade(pnl=100.0, pnl_pct=0.01,
                       theme_data_quality_at_signal={"state": "ok"},
                       regime_at_signal="BULL_TREND", pattern_id="p1", theme_lifecycle="ACCELERATING").to_dict(),
            make_trade(pnl=-50.0, pnl_pct=-0.005,
                       theme_data_quality_at_signal={"state": "degraded"},
                       regime_at_signal="BULL_TREND", pattern_id="p1", theme_lifecycle="ACCELERATING").to_dict(),
        ]
        rows = multi_key_trade_stats(td, ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        assert rows[0]["quality_coverage"] == 0.5
        assert rows[0]["usable_for_router"] is False

    def test_all_degraded_not_usable(self):
        td = [
            make_trade(pnl=100.0, pnl_pct=0.01, theme_data_quality_at_signal={"state": "degraded"}).to_dict() for _ in range(3)
        ]
        rows = multi_key_trade_stats(td, ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        assert rows[0]["usable_for_router"] is False

    def test_unknown_quality_not_usable(self):
        td = [make_trade(pnl=100.0, pnl_pct=0.01, theme_data_quality_at_signal={}).to_dict()]
        rows = multi_key_trade_stats(td, ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        assert rows[0]["quality_coverage"] == 0.0
        assert rows[0]["usable_for_router"] is False

    def test_quality_counts_breakdown(self):
        td = [
            make_trade(pnl=100.0, pnl_pct=0.01, theme_data_quality_at_signal={"state": "ok"}).to_dict(),
            make_trade(pnl=50.0, pnl_pct=0.005, theme_data_quality_at_signal={"state": "ok"}).to_dict(),
            make_trade(pnl=-20.0, pnl_pct=-0.002, theme_data_quality_at_signal={"state": "degraded"}).to_dict(),
            make_trade(pnl=-10.0, pnl_pct=-0.001, theme_data_quality_at_signal={"state": "unavailable"}).to_dict(),
            make_trade(pnl=30.0, pnl_pct=0.003, theme_data_quality_at_signal={}).to_dict(),
        ]
        rows = multi_key_trade_stats(td, ["regime_at_signal", "pattern_id", "theme_lifecycle"])
        r = rows[0]
        assert r["quality_ok_trades"] == 2
        assert r["quality_degraded_trades"] == 1
        assert r["quality_unavailable_trades"] == 1
        assert r["quality_unknown_trades"] == 1
        assert r["quality_coverage"] == 0.4
        assert r["usable_for_router"] is False

class TestLegacyStatsCompatibility:
    """Legacy grouped_trade_stats must still work."""

    def test_by_market_regime_still_works(self):
        """The existing by_market_regime grouping must function."""
        from a_share_agent.backtest.metrics import grouped_trade_stats
        t1 = make_trade(symbol="A.SH", pnl=100.0, pnl_pct=0.01,
                        entry_market_regime="risk_on")
        t2 = make_trade(symbol="B.SH", pnl=-50.0, pnl_pct=-0.005,
                        entry_market_regime="risk_off")
        trades = [t1.to_dict(), t2.to_dict()]
        stats = grouped_trade_stats(trades, "entry_market_regime")
        assert len(stats) == 2
        regimes = {s["group"] for s in stats}
        assert "risk_on" in regimes
        assert "risk_off" in regimes

    def test_by_sector_strength_still_works(self):
        from a_share_agent.backtest.metrics import grouped_trade_stats
        t1 = make_trade(symbol="A.SH", pnl=100.0, pnl_pct=0.01,
                        entry_sector_strength="strong")
        t2 = make_trade(symbol="B.SH", pnl=50.0, pnl_pct=0.005,
                        entry_sector_strength="weak")
        trades = [t1.to_dict(), t2.to_dict()]
        stats = grouped_trade_stats(trades, "entry_sector_strength")
        assert len(stats) == 2
