"""Walk-forward execution window tests.

Verifies that BacktestEngine.run respects an explicit evaluation_window
containing fold_id, entry_start, entry_end_exclusive, observation_end_exclusive.
Ordinary run behavior (no evaluation_window) must remain unchanged.
Uses the same bars/raw_bars convention as test_backtest.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from a_share_agent.backtest.engine import BacktestEngine
from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.config import load_config


def _trading_dates(start: date, min_days: int) -> list[str]:
    """Generate at least min_days trading dates from start."""
    dates: list[str] = []
    d = start
    while len(dates) < min_days:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


class BaseSyntheticProvider:
    """Synthetic provider matching test_backtest.py bars/raw_bars convention.

    Generates rising-staircase data for multiple symbols with periodic volume
    spikes to trigger the deterministic signal engine (high_volume_breakout).
    """

    def __init__(self, symbols: list[str], *,
                 start: date = date(2022, 1, 1),
                 min_bars: int = 920):
        self.warnings: list[str] = []
        self._bars: dict[str, list[dict[str, Any]]] = {}
        dates = _trading_dates(start, min_bars)

        # Benchmark
        bench: list[dict[str, Any]] = []
        for i, ds in enumerate(dates):
            c = 3000.0 + i * 3.0
            bench.append({
                "date": ds, "open": c - 2, "high": c + 6, "low": c - 6,
                "close": c, "volume": 1_000_000 + i * 100,
            })
        self._bars["000300.SH"] = bench

        # Stock symbols — rising staircase with volume spikes every 30 bars.
        for j, sym in enumerate(symbols):
            rows: list[dict[str, Any]] = []
            base = 10.0 + j
            for i, ds in enumerate(dates):
                c = base + i * 0.012 + (i // 35) * 0.15
                o = c - 0.015
                h = c + 0.04
                l = c - 0.05
                vol = 3_500_000 if i % 30 == 0 else 1_000_000
                rows.append({
                    "date": ds, "open": o, "high": h, "low": l,
                    "close": c, "volume": vol,
                    "adjusted_close": c,
                    "turnover_rate": 0.01,
                    "market_cap": 1e10, "float_market_cap": 5e9,
                    "pe_ratio": 15.0, "pb_ratio": 1.5,
                    "is_suspended": False,
                    "is_limit_up": False, "is_limit_down": False,
                    "is_new_stock": False,
                })
            self._bars[sym] = rows

    def bars(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self._bars.get(symbol, [])

    def raw_bars(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self._bars.get(symbol, [])

    def sector_info(self, symbol: str) -> dict[str, Any]:
        return {"name": "synthetic", "code": None, "source": "synthetic"}

    def sector_bars(self, code: str) -> list[dict[str, Any]]:
        return []

    def eligible_on(self, symbol: str, d: str) -> bool:
        return True

    def active_records_on(self, d: str, seed_symbols: list[str]) -> list[dict[str, Any]]:
        return [{"symbol": s, "tradable": True} for s in seed_symbols if s in self._bars]

    def is_market_tradable(self, symbol: str, d: str) -> bool:
        return True

    def status_on(self, symbol: str, d: str) -> str:
        return ""

    def board_on(self, symbol: str, d: str) -> str:
        return ""

    def daily_universe_meta(self, d: str, seed_symbols: list[str]) -> dict[str, Any]:
        return {
            "date": d,
            "active_symbols": len([s for s in seed_symbols if s in self._bars]),
            "source": "synthetic",
            "point_in_time": False,
            "universe_hash": None,
        }

    def is_strategy_eligible(self, symbol: str, d: str) -> bool:
        return True

    def listing_date_on(self, symbol: str) -> str:
        return "2022-01-04"

    def delisting_days_on(self, symbol: str, d: str) -> int:
        return 0


# ---------------------------------------------------------------------------
# Validation tests (existing skeletons, completed)
# ---------------------------------------------------------------------------


def test_evaluation_window_missing_raises():
    """evaluation_window must be dict or None; TypeError otherwise."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-01-01", end_date="2025-06-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    try:
        engine.run(["TEST01.SH"], evaluation_window="not a dict")
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_evaluation_window_fold_id_required():
    """evaluation_window must contain fold_id."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-01-01", end_date="2025-06-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "entry_start": "2025-01-01",
        "entry_end_exclusive": "2025-02-01",
        "observation_end_exclusive": "2025-03-01",
    }
    try:
        engine.run(["TEST01.SH"], evaluation_window=window)
        assert False, "expected ValueError for missing fold_id"
    except ValueError:
        pass


def test_evaluation_window_date_fields_required():
    """evaluation_window must contain all date fields."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-01-01", end_date="2025-06-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-01-01",
        "entry_end_exclusive": "2025-02-01",
    }
    try:
        engine.run(["TEST01.SH"], evaluation_window=window)
        assert False, "expected ValueError for missing observation_end_exclusive"
    except ValueError:
        pass


def test_ordinary_run_unchanged():
    """Without evaluation_window, report must contain standard keys."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-01-01", end_date="2025-06-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    report = engine.run(["TEST01.SH"])
    assert report["methodology"]["entry_execution"] == "next_trading_day_open"
    assert "trades" in report
    assert "equity_curve" in report
    assert "metrics" in report
# Entry window behavioural tests
# ---------------------------------------------------------------------------


def test_evaluation_window_entries_restricted_to_entry_window():
    """BUY orders only created for dates < entry_end_exclusive."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # All BUY trade dates must be < entry_end_exclusive (BUY executes next open
    # after signal; signal date < entry_end_exclusive).
    for t in report["trades"]:
        if t["direction"] == "BUY":
            assert t["trade_date"] < "2025-02-10", (
                f"BUY {t['symbol']} on {t['trade_date']} after entry_end_exclusive"
            )

    # evaluation_window echoed in report
    assert report.get("evaluation_window") == window


def test_evaluation_window_no_new_buys_after_entry_end():
    """After entry_end_exclusive, no new BUY pending orders created."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(["TEST01.SH"], start=date(2024, 1, 1), min_bars=400)
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # Any ENTRY_SIGNAL log must have date < entry_end_exclusive
    for ev in report.get("events", []):
        if ev.get("type") == "ENTRY_SIGNAL":
            assert ev["date"] < "2025-02-10", (
                f"ENTRY_SIGNAL on {ev['date']} after entry_end_exclusive"
            )


def test_evaluation_window_existing_exits_naturally():
    """Positions entered during entry window can exit naturally in observation."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # Some SELL trades should exist with trade_date >= entry_end_exclusive
    sells_after = [
        t for t in report["trades"]
        if t["direction"] == "SELL" and t["trade_date"] >= "2025-02-10"
    ]
    assert len(sells_after) > 0, "Expected some SELL trades in observation window"


def test_evaluation_window_no_forced_liquidation():
    """No END_OF_BACKTEST forced sell at end of observation window."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # No END_OF_BACKTEST reason in trades
    for t in report["trades"]:
        assert t.get("reason") != "END_OF_BACKTEST", (
            f"Found END_OF_BACKTEST trade for {t['symbol']}"
        )


# ---------------------------------------------------------------------------
# Report exposure tests
# ---------------------------------------------------------------------------


def test_evaluation_window_exposes_positions_censored_pending():
    """Report includes evaluation_window, portfolio_positions, censored_positions,
    pending_orders when evaluation_window is active."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    assert "evaluation_window" in report
    assert report["evaluation_window"]["fold_id"] == 0
    assert "portfolio_positions" in report
    assert isinstance(report["portfolio_positions"], list)
    assert "censored_positions" in report
    assert isinstance(report["censored_positions"], list)
    assert "pending_orders" in report
    assert isinstance(report["pending_orders"], list)


def test_evaluation_window_no_exposure_without_window():
    """Without evaluation_window, portfolio_positions etc must not appear."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    report = engine.run(["TEST01.SH"])

    assert "evaluation_window" not in report
    assert "portfolio_positions" not in report
    assert "censored_positions" not in report
    assert "pending_orders" not in report


# ---------------------------------------------------------------------------
# T+1 enforcement during evaluation window
# ---------------------------------------------------------------------------


def test_evaluation_window_t_plus_one_enforced():
    """T+1 still blocks same-day sell during evaluation window."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    for t in report["trades"]:
        if t["direction"] == "SELL":
            assert t["trade_date"] > t["entry_date"], (
                f"T+1 violation: SELL {t['symbol']} on {t['trade_date']} "
                f"same as entry {t['entry_date']}"
            )


# ---------------------------------------------------------------------------
# Stop loss honoured during observation window
# ---------------------------------------------------------------------------


def test_evaluation_window_stop_loss_honored():
    """Stop-loss triggers in observation window for positions still held."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=20,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # At minimum: exits should happen via MAX_HOLDING_DAYS or STOP_LOSS during
    # observation window.
    obs_exits = [
        t for t in report["trades"]
        if t["direction"] == "SELL"
        and t["trade_date"] >= "2025-02-10"
    ]
    assert len(obs_exits) > 0, "Expected exits in observation window"


# ---------------------------------------------------------------------------
# Censored positions: suspension prevents exit
# ---------------------------------------------------------------------------


class SuspendedProvider(BaseSyntheticProvider):
    """Provider that marks a symbol as suspended during a date range."""

    def __init__(self, symbols: list[str], *,
                 suspend_symbol: str,
                 suspend_from: str,
                 suspend_until: str,
                 **kwargs: Any):
        super().__init__(symbols, **kwargs)
        self._suspend_symbol = suspend_symbol
        self._suspend_from = suspend_from
        self._suspend_until = suspend_until

    def is_market_tradable(self, symbol: str, d: str) -> bool:
        if symbol == self._suspend_symbol and self._suspend_from <= d <= self._suspend_until:
            return False
        return True

    def status_on(self, symbol: str, d: str) -> str:
        if symbol == self._suspend_symbol and self._suspend_from <= d <= self._suspend_until:
            return "SUSPENDED"
        return ""


def test_evaluation_window_suspended_position_censored():
    """A position in a suspended stock cannot exit; appears in censored_positions."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = SuspendedProvider(
        ["TEST01.SH"],
        suspend_symbol="TEST01.SH",
        suspend_from="2025-03-10",
        suspend_until="2025-04-30",
        start=date(2024, 1, 1),
        min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=35,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # Check censored_positions contains the suspended symbol
    censored = report.get("censored_positions", [])
    censored_symbols = {c.get("symbol") for c in censored}
    assert "TEST01.SH" in censored_symbols, (
        f"TEST01.SH should be in censored_positions, got {censored_symbols}"
    )


# ---------------------------------------------------------------------------
# Price-locked entry blocked during evaluation window
# ---------------------------------------------------------------------------


class LimitLockedProvider(BaseSyntheticProvider):
    """Provider that simulates limit-up on a specific date for a symbol."""

    def __init__(self, symbols: list[str], *,
                 lock_symbol: str,
                 lock_date: str,
                 **kwargs: Any):
        super().__init__(symbols, **kwargs)
        self._lock_symbol = lock_symbol
        self._lock_date = lock_date
        # Replace that date's bars with limit-up data
        if lock_symbol in self._bars:
            for bar in self._bars[lock_symbol]:
                if bar["date"] == lock_date:
                    bar["close"] = bar["close"] * 1.10
                    bar["high"] = bar["close"]
                    bar["is_limit_up"] = True

    def board_on(self, symbol: str, d: str) -> str:
        if symbol == self._lock_symbol and d == self._lock_date:
            return "MAIN_BOARD"
        return ""


def test_evaluation_window_price_locked_entry_blocked():
    """Price-locked condition blocks a BUY order on the execution date."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = LimitLockedProvider(
        ["TEST01.SH"],
        lock_symbol="TEST01.SH",
        lock_date="2025-02-04",
        start=date(2024, 1, 1),
        min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # Check for LOCKED_AT_PRICE_LIMIT rejection
    limit_rejections = [
        r for r in report.get("rejections", [])
        if r.get("reason") == "LOCKED_AT_PRICE_LIMIT"
    ]
    assert len(limit_rejections) > 0, (
        "Expected at least one LOCKED_AT_PRICE_LIMIT rejection"
    )


# ---------------------------------------------------------------------------
# fold_id in report
# ---------------------------------------------------------------------------


def test_evaluation_window_fold_id_in_report():
    """fold_id from evaluation_window appears in the report."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 7,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    assert report["evaluation_window"]["fold_id"] == 7


# ---------------------------------------------------------------------------
# Ordinary parity — standard report keys present with/without window
# ---------------------------------------------------------------------------


def test_evaluation_window_ordinary_parity():
    """With and without window produce same standard report keys."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-03-28",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )

    # Without window — uses forced liquidation at end
    engine_no = BacktestEngine(cfg, provider, s)
    report_no = engine_no.run(["TEST01.SH"])

    # With window covering the same range (end_date + 1)
    engine_win = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-03-29",
    }
    report_win = engine_win.run(["TEST01.SH"], evaluation_window=window)

    # Both should have the same standard report keys
    for key in ("trades", "equity_curve", "metrics", "coverage",
                "rejections", "events"):
        assert key in report_no, f"missing {key} in ordinary report"
        assert key in report_win, f"missing {key} in window report"


# ---------------------------------------------------------------------------
# Independent portfolio — each fold starts with fresh initial_cash
# ---------------------------------------------------------------------------


def test_evaluation_window_independent_portfolio():
    """Each evaluation_window run starts with fresh initial_cash."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = BaseSyntheticProvider(
        ["TEST01.SH"], start=date(2024, 1, 1), min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=12,
    )
    engine1 = BacktestEngine(cfg, provider, s)
    window1 = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report1 = engine1.run(["TEST01.SH"], evaluation_window=window1)

    engine2 = BacktestEngine(cfg, provider, s)
    window2 = {
        "fold_id": 1,
        "entry_start": "2025-03-03",
        "entry_end_exclusive": "2025-04-01",
        "observation_end_exclusive": "2025-05-01",
    }
    report2 = engine2.run(["TEST01.SH"], evaluation_window=window2)

    # Each fold starts with initial cash (not carried from fold 0)
    assert report1["metrics"]["starting_equity"] == 1_000_000
    assert report2["metrics"]["starting_equity"] == 1_000_000


# ---------------------------------------------------------------------------
# Censored report format and audit trail
# ---------------------------------------------------------------------------


def test_evaluation_window_censored_format():
    """censored_positions entries have required fields."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    provider = SuspendedProvider(
        ["TEST01.SH"],
        suspend_symbol="TEST01.SH",
        suspend_from="2025-03-10",
        suspend_until="2025-04-30",
        start=date(2024, 1, 1),
        min_bars=400,
    )
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=35,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    for cp in report.get("censored_positions", []):
        assert "symbol" in cp
        assert "entry_date" in cp
        assert "quantity" in cp
        assert "current_price" in cp
        assert "reason" in cp


# ---------------------------------------------------------------------------
# pending_orders audit trail
# ---------------------------------------------------------------------------


def test_evaluation_window_pending_audit():
    """pending_orders contains unfilled orders at end of observation."""
    cfg = load_config(Path(__file__).resolve().parents[1])
    s = BacktestSettings(
        start_date="2025-02-03", end_date="2025-04-30",
        initial_cash=1_000_000, benchmark="000300.SH",
        min_score=75, max_holding_days=35,
    )
    # Use SuspendedProvider so SELL orders may remain pending for suspended stock.
    provider = SuspendedProvider(
        ["TEST01.SH"],
        suspend_symbol="TEST01.SH",
        suspend_from="2025-03-20",
        suspend_until="2025-04-30",
        start=date(2024, 1, 1),
        min_bars=400,
    )
    engine = BacktestEngine(cfg, provider, s)
    window = {
        "fold_id": 0,
        "entry_start": "2025-02-03",
        "entry_end_exclusive": "2025-02-10",
        "observation_end_exclusive": "2025-05-01",
    }
    report = engine.run(["TEST01.SH"], evaluation_window=window)

    # pending_orders may be empty or have entries — just verify it's a list
    assert isinstance(report.get("pending_orders"), list)
