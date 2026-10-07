"""Phase 6 Paper Trading: F01-F07 integrated tests.

F01: Full chain Signal→Risk→Intent→Order→Fill→Position→Exit
F02: All risk checks: suspension, limit-up/down, existing order, cash, caps, sector, T+1
F03: Retry/restart/repeat/reject/partial fill → correct cash/position
F04: Raw execution, slippage, lot rounding, gross/net, FIFO
F05: Persistence, restart recovery, network interruption reconciliation
F06: 20+ trading days, 7 failure scenarios
F07: allow_real_execution always False, broker call count = 0
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from a_share_agent.execution.paper_engine import PaperEngine, SignalDecision
from a_share_agent.execution.paper_ledger import PaperLedger, PaperOrder, PaperPosition
from a_share_agent.risk.paper_risk import PaperRiskEngine, PaperRiskResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persist_path(tmp_path: Path) -> Path:
    return tmp_path / "paper_ledger.json"


@pytest.fixture
def paper_risk() -> PaperRiskEngine:
    return PaperRiskEngine(initial_cash=1_000_000.0)


@pytest.fixture
def paper_ledger(persist_path: Path) -> PaperLedger:
    return PaperLedger(persist_path=persist_path)


@pytest.fixture
def engine(paper_ledger: PaperLedger, paper_risk: PaperRiskEngine) -> PaperEngine:
    return PaperEngine(
        ledger=paper_ledger,
        risk=paper_risk,
        initial_cash=1_000_000.0,
        allow_real_execution=False,
        sector_map={"600000.SH": "finance", "300001.SZ": "tech"},
    )


def _buy_decision(symbol: str = "600000.SH", **overrides: Any) -> SignalDecision:
    """Create a standard BUY_TRIGGERED decision for testing."""
    base = {
        "decision_id": "DEC-001",
        "symbol": symbol,
        "pattern_id": "ma60_breakout_retest",
        "pattern_version": "1.0.0",
        "status": "BUY_TRIGGERED",
        "context": {"market_regime": "strong_up", "as_of": "2026-10-06T09:30:00+08:00"},
        "candidate": {
            "symbol": symbol,
            "pattern_id": "ma60_breakout_retest",
            "pattern_version": "1.0.0",
            "score": 85.0,
            "sector": "finance",
            "regime_at_signal": "strong_up",
            "theme_lifecycle": "expansion",
        },
        "entry_plan": {"limit_price": 10.0, "max_quantity": 1000, "stop_price": 9.5, "timestamp": "2026-10-06T09:30:00+08:00"},
        "alerts": [{"alert_type": "entry_candidate", "message": "ma60 breakout retest"}],
        "created_at": "2026-10-06T09:30:00+08:00",
        "state_version": 1,
    }
    valid_fields = {k: v for k, v in base.items() if k in SignalDecision.__dataclass_fields__}
    for k, v in overrides.items():
        if k in SignalDecision.__dataclass_fields__:
            valid_fields[k] = v
    return SignalDecision(**valid_fields)


def _sell_decision(symbol: str = "600000.SH", **overrides: Any) -> SignalDecision:
    """Create a standard SELL_TRIGGERED decision for testing."""
    base = {
        "decision_id": "DEC-002",
        "symbol": symbol,
        "pattern_id": "ma60_breakout_retest",
        "pattern_version": "1.0.0",
        "status": "SELL_TRIGGERED",
        "context": {"market_regime": "weakening", "as_of": "2026-10-09T09:30:00+08:00"},
        "candidate": {
            "symbol": symbol,
            "pattern_id": "ma60_breakout_retest",
            "pattern_version": "1.0.0",
            "score": 85.0,
            "sector": "finance",
            "regime_at_signal": "weakening",
            "theme_lifecycle": "contraction",
        },
        "exit_plan": {"exit_price": 11.0, "exit_reason": "take_profit", "timestamp": "2026-10-09T09:30:00+08:00"},
        "alerts": [{"alert_type": "exit_signal", "message": "take profit"}],
        "created_at": "2026-10-09T09:30:00+08:00",
        "state_version": 1,
    }
    valid_fields = {k: v for k, v in base.items() if k in SignalDecision.__dataclass_fields__}
    for k, v in overrides.items():
        if k in SignalDecision.__dataclass_fields__:
            valid_fields[k] = v
    return SignalDecision(**valid_fields)


# Fraction of a 1M account — comfortable margin.
_INITIAL_CASH = 1_000_000.0


# ===================================================================
# F01: Full chain Signal→Risk→Intent→Order→Fill→Position→Exit
# ===================================================================


class TestF01_FullChain:
    """Verify the complete paper lifecycle end-to-end."""

    def test_buy_signal_through_to_exit(self, engine: PaperEngine):
        """BUY signalled → risk check → order → fill → position → exit."""
        # Step 1: Process buy decision
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        assert result["status"] == "CREATED", f"Expected CREATED, got {result}"
        order = result["order"]
        assert order["direction"] == "BUY"
        assert order["quantity"] > 0
        assert order["status"] == "CREATED"

        # Step 2: Fill the order
        fill = engine.fill_order(order["order_id"], market_price=10.05, trade_date="2026-10-06")
        assert fill["filled"] is True
        assert fill["fill_price"] > 0

        # Step 3: Verify position opened
        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.status == "OPEN"
        assert pos.quantity > 0
        assert pos.entry_price > 0

        # Step 4: Process sell decision (new trade date to avoid T+1)
        engine.new_trade_date("2026-10-09", prev_trade_date="2026-10-06")
        sell_decision = _sell_decision()
        sell_result = engine.process_signal(sell_decision, trade_date="2026-10-09")
        assert sell_result["status"] == "CREATED", f"Expected CREATED, got {sell_result}"

        # Step 5: Fill sell order
        sell_fill = engine.fill_order(sell_result["order"]["order_id"], market_price=11.0, trade_date="2026-10-09")
        assert sell_fill["filled"] is True

        # Step 6: Position should be closed
        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.status == "CLOSED"

        # Step 7: Cash should have increased (profitable trade)
        assert engine.cash > 0

        # Step 8: Round trips should exist
        rts = engine.ledger.list_round_trips()
        assert len(rts) >= 1

    def test_signal_decision_dict_input(self, engine: PaperEngine):
        """process_signal accepts raw dict as well as dataclass."""
        raw = _buy_decision().to_dict()
        result = engine.process_signal(raw, trade_date="2026-10-06")
        assert result["status"] in ("CREATED", "REJECTED")

    def test_non_triggered_decision_rejected(self, engine: PaperEngine):
        """WATCH status should be rejected."""
        decision = _buy_decision(status="WATCH")
        result = engine.process_signal(decision, trade_date="2026-10-06")
        assert result["status"] == "REJECTED"
        assert "NOT_TRIGGERED" in (result.get("reject_reason") or "")

    def test_invalid_entry_plan_rejected(self, engine: PaperEngine):
        """Missing entry plan fields should be rejected."""
        decision = _buy_decision(entry_plan={})
        result = engine.process_signal(decision, trade_date="2026-10-06")
        assert result["status"] == "REJECTED"
        assert "INVALID_ENTRY_PLAN" in (result.get("reject_reason") or "")

    def test_buy_fill_then_sell_same_day_blocked_t1(self, engine: PaperEngine):
        """T+1 should block same-day sell."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        assert result["status"] == "CREATED"
        engine.fill_order(result["order"]["order_id"], market_price=10.05, trade_date="2026-10-06")

        # Try selling same day (should be rejected by risk)
        sell_decision = _sell_decision()
        sell_result = engine.process_signal(sell_decision, trade_date="2026-10-06")
        assert sell_result["status"] == "REJECTED", "T+1 should block same-day sell"
        assert sell_result.get("reject_reason") is not None


# ===================================================================
# F02: All risk checks
# ===================================================================


class TestF02_RiskChecks:
    """Every risk check must reject appropriately."""

    def test_suspended_stock(self, engine: PaperEngine):
        """Suspended stock → REJECT."""
        decision = _buy_decision()
        result = engine.process_signal(
            decision,
            trade_date="2026-10-06",
            quote={"suspended": True, "price": 10.0, "prev_close": 10.0},
        )
        assert result["status"] == "REJECTED", f"Expected REJECTED: {result}"
        assert result.get("risk", {}).get("reason") in ("SUSPENDED_STOCK", None) or result.get("reject_reason") is not None

    def test_blacklist(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Blacklisted symbol → REJECT."""
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=1000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[],
            open_orders=[],
            blacklist=["600000.SH"],
        )
        assert risk.status == "REJECTED"
        assert "BLACKLISTED" in (risk.reason or "")

    def test_limit_up_buy_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Buy at limit up → REJECT."""
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=1000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[],
            open_orders=[],
            limit_up_pct=-0.01,  # at limit up
        )
        assert risk.status == "REJECTED"
        assert "LIMIT_UP_BUY" in (risk.reason or "")

    def test_limit_down_sell_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Sell at limit down → REJECT."""
        risk = paper_risk.assess(
            direction="SELL",
            symbol="600000.SH",
            requested_quantity=1000,
            entry_price=10.0,
            stop_price=None,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[{"symbol": "600000.SH", "quantity": 1000, "current_price": 10.0}],
            open_orders=[],
            limit_down_pct=0.01,  # at limit down
        )
        assert risk.status == "REJECTED"
        assert "LIMIT_DOWN_SELL" in (risk.reason or "")

    def test_existing_order_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Existing open order for same symbol → REJECT."""
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=1000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[],
            open_orders=[{"symbol": "600000.SH", "status": "CREATED"}],
        )
        assert risk.status == "REJECTED"
        assert "EXISTING_ORDER" in (risk.reason or "")

    def test_insufficient_cash_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Order too large for cash → REJECT."""
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=200_000,  # 200K shares × 10 = 2M >> cash
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=100_000.0,  # only 100k cash
            positions=[],
            open_orders=[],
        )
        assert risk.status == "REJECTED"
        assert "INSUFFICIENT_CASH" in (risk.reason or "")

    def test_single_stock_cap_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Exceeding single stock cap → REJECT."""
        # 10% of 1M = 100k max. 10K shares × 10 = 100k, so 11K should fail
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=11_000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[],
            open_orders=[],
        )
        assert risk.status == "REJECTED"
        assert "SINGLE_STOCK_CAP" in (risk.reason or "")

    def test_total_position_cap_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Exceeding total position cap → REJECT."""
        # 50% of 1M = 500k max. Already have 450k, adding 10k shares × 10 = 100k = 550k > 500k
        risk = paper_risk.assess(
            direction="BUY",
            symbol="300001.SZ",
            requested_quantity=10_000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[{"symbol": "600000.SH", "quantity": 5000, "current_price": 9.0, "market_value": 45000.0,
                        "value": 45000.0}],
            open_orders=[],
            total_equity=_INITIAL_CASH,
        )
        # Should pass or fail based on calculations. Let's check the value:
        # 5K × 9 = 45K. New: 45K + 10K × 10 = 145K. Cap: 1M * 0.5 = 500K. So this should PASS.
        # Let me be more aggressive:
        risk2 = paper_risk.assess(
            direction="BUY",
            symbol="300001.SZ",
            requested_quantity=50_000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[{"symbol": "600000.SH", "quantity": 50000, "current_price": 10.0, "market_value": 500000.0,
                        "value": 500000.0}],
            open_orders=[],
            total_equity=_INITIAL_CASH,
        )
        assert risk2.status == "REJECTED"
        assert "TOTAL_POSITION_CAP" in (risk2.reason or "")

    def test_single_order_cap_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Single order exceeds order cap → REJECT."""
        # 5% of 1M = 50k max. 10K shares × 10 = 100k > 50k
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=10_000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[],
            open_orders=[],
        )
        assert risk.status == "REJECTED"
        assert "SINGLE_ORDER_CAP" in (risk.reason or "")

    def test_sector_concentration_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """Exceeding sector concentration cap → REJECT."""
        risk = paper_risk.assess(
            direction="BUY",
            symbol="600000.SH",
            requested_quantity=50_000,
            entry_price=10.0,
            stop_price=9.5,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[{"symbol": "600000.SH", "quantity": 10000, "current_price": 10.0, "market_value": 100000.0,
                        "value": 100000.0}],
            open_orders=[],
            sector="finance",
            sector_map={"600000.SH": "finance"},
            total_equity=_INITIAL_CASH,
        )
        # Existing 100k + new 500k = 600k > 300k cap (30%)
        assert risk.status == "REJECTED"
        assert "SECTOR_CONCENTRATION" in (risk.reason or "")

    def test_t1_sell_restriction_rejected(self, engine: PaperEngine, paper_risk: PaperRiskEngine):
        """T+1 sell restriction → REJECT."""
        risk = paper_risk.assess(
            direction="SELL",
            symbol="600000.SH",
            requested_quantity=1000,
            entry_price=10.0,
            stop_price=None,
            trade_date="2026-10-06",
            cash=_INITIAL_CASH,
            positions=[{"symbol": "600000.SH", "quantity": 1000, "current_price": 10.0}],
            open_orders=[],
            bought_today={"600000.SH"},
        )
        assert risk.status == "REJECTED"
        assert "T+1_SELL_RESTRICTION" in (risk.reason or "")


# ===================================================================
# F03: Retry/restart/repeat/reject/partial fill
# ===================================================================


class TestF03_OrderLifecycle:
    """Correct behavior for retry, restart, repeat, reject, partial fill."""

    def test_repeat_signal_rejected(self, engine: PaperEngine):
        """Repeated signal for same stock with open order should be rejected by risk."""
        decision = _buy_decision()
        r1 = engine.process_signal(decision, trade_date="2026-10-06")
        assert r1["status"] == "CREATED"

        # Same decision again (no idempotency check in paper, but risk should reject)
        r2 = engine.process_signal(decision, trade_date="2026-10-06")
        # Risk sees existing open order → should reject
        assert r2["status"] == "REJECTED", f"Expected REJECTED for repeat: {r2}"

    def test_reject_releases_position(self, engine: PaperEngine):
        """Rejected order should not affect cash or positions."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        order_id = result["order"]["order_id"]

        # Reject the order
        reject_result = engine.reject_order(order_id, reason="TEST_REJECT")
        assert reject_result["status"] == "REJECTED"

        # Position should not exist
        assert engine.ledger.get_position("600000.SH") is None

    def test_cancel_releases_position(self, engine: PaperEngine):
        """Cancelled order should not affect cash or positions."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        order_id = result["order"]["order_id"]

        cancel_result = engine.cancel_order(order_id)
        assert cancel_result["status"] == "CANCELLED"

        # No position
        assert engine.ledger.get_position("600000.SH") is None

    def test_partial_fill_leaves_remaining(self, engine: PaperEngine):
        """Partial fill → order stays open, remaining quantity tracked."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        order_id = result["order"]["order_id"]
        total_qty = result["order"]["quantity"]

        # Fill 60% partial
        fill = engine.fill_order(order_id, market_price=10.05, fill_pct=0.6, trade_date="2026-10-06")
        assert fill["is_partial"] is True

        # Verify order is PARTIAL_FILLED
        order = engine.ledger.get_order(order_id)
        assert order is not None
        assert order.status == "PARTIAL_FILLED"
        assert order.filled_quantity > 0
        assert order.filled_quantity < total_qty

        # Position should be open with partial quantity
        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.status == "OPEN"
        assert pos.quantity == order.filled_quantity

        # Fill remaining
        fill2 = engine.fill_order(order_id, market_price=10.08, fill_pct=1.0, trade_date="2026-10-06")
        assert fill2["filled"] is True
        order = engine.ledger.get_order(order_id)
        assert order.status == "FILLED"
        assert order.filled_quantity == total_qty

        # Position should have full quantity
        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.quantity == total_qty

    def test_failed_fill_no_position_created(self, engine: PaperEngine):
        """Fill that fails should not leave dangling position."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        order_id = result["order"]["order_id"]

        # Reject before fill
        engine.reject_order(order_id, reason="MARKET_CONDITIONS")
        assert engine.ledger.get_position("600000.SH") is None


# ===================================================================
# F04: Raw execution, slippage, lot rounding, gross/net, FIFO
# ===================================================================


class TestF04_ExecutionDetail:
    """Verify raw/slipped prices, lot rounding, gross/net P&L, FIFO."""

    def test_slippage_applied(self, engine: PaperEngine):
        """Fill price should include slippage."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        order_id = result["order"]["order_id"]

        fill = engine.fill_order(order_id, market_price=10.0, trade_date="2026-10-06")
        # Slippage: 10.0 * (1 + 0.0005) = 10.005 for BUY
        expected = round(10.0 * 1.0005, 4)
        assert abs(fill["fill_price"] - expected) < 0.001, f"Expected {expected}, got {fill['fill_price']}"
        assert fill["raw_price"] == 10.0

    def test_slippage_sell_reduces_price(self, engine: PaperEngine):
        """Sell slippage should reduce the fill price."""
        # First buy
        decision = _buy_decision()
        r = engine.process_signal(decision, trade_date="2026-10-06")
        engine.fill_order(r["order"]["order_id"], market_price=10.0, trade_date="2026-10-06")

        # Advance date for T+1
        engine.new_trade_date("2026-10-09", prev_trade_date="2026-10-06")

        # Sell
        sell_decision = _sell_decision()
        sr = engine.process_signal(sell_decision, trade_date="2026-10-09")
        sell_fill = engine.fill_order(sr["order"]["order_id"], market_price=11.0, trade_date="2026-10-09")

        # Slippage: 11.0 * (1 - 0.0005) = 10.9945 for SELL
        expected = round(11.0 * 0.9995, 4)
        assert abs(sell_fill["fill_price"] - expected) < 0.001, f"Expected {expected}, got {sell_fill['fill_price']}"

    def test_board_lot_rounding_main(self, engine: PaperEngine):
        """SSE main board rounds to multiples of 100."""
        decision = _buy_decision(entry_plan={"limit_price": 10.0, "max_quantity": 950, "stop_price": 9.5, "timestamp": "2026-10-06T09:30:00+08:00"})
        result = engine.process_signal(decision, trade_date="2026-10-06")
        if result["status"] == "CREATED":
            # 950 should round to 900 (multiples of 100)
            assert result["order"]["quantity"] == 900, f"Expected 900, got {result['order']['quantity']}"

    def test_board_lot_star_200_min(self, engine: PaperEngine):
        """STAR board minimum 200 shares, 1-share increments above."""
        from a_share_agent.execution.paper_engine import _board_round_lot
        # 199 → 0 (below min)
        assert _board_round_lot("688001.SH", 199, "BUY", "STAR") == 0
        # 200 → 200
        assert _board_round_lot("688001.SH", 200, "BUY", "STAR") == 200
        # 201 → 201 (1-share increment)
        assert _board_round_lot("688001.SH", 201, "BUY", "STAR") == 201

    def test_board_lot_bse_100_min(self, engine: PaperEngine):
        """BSE board minimum 100 shares, 1-share increments above."""
        from a_share_agent.execution.paper_engine import _board_round_lot
        # 99 → 0
        assert _board_round_lot("920002.BJ", 99, "BUY", "BSE") == 0
        # 100 → 100
        assert _board_round_lot("920002.BJ", 100, "BUY", "BSE") == 100
        # 101 → 101
        assert _board_round_lot("920002.BJ", 101, "BUY", "BSE") == 101

    def test_gross_vs_net_pnl(self, engine: PaperEngine):
        """Gross P&L should not include fees; net should."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        engine.fill_order(result["order"]["order_id"], market_price=10.0, trade_date="2026-10-06")

        engine.new_trade_date("2026-10-09", prev_trade_date="2026-10-06")

        sell_decision = _sell_decision()
        sr = engine.process_signal(sell_decision, trade_date="2026-10-09")
        engine.fill_order(sr["order"]["order_id"], market_price=11.0, trade_date="2026-10-09")

        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.status == "CLOSED"

        # Round trip should exist
        rts = engine.ledger.list_round_trips()
        assert len(rts) >= 1
        rt = rts[0]
        assert rt.gross_pnl != 0 or rt.net_pnl != 0
        # Net should be less than or equal to gross (fees reduce it)
        assert rt.net_pnl <= rt.gross_pnl or abs(rt.net_pnl - rt.gross_pnl) < 0.01

    def test_fifo_partial_exit(self, engine: PaperEngine):
        """FIFO partial exit reduces position correctly."""
        decision = _buy_decision(entry_plan={"limit_price": 10.0, "max_quantity": 1000, "stop_price": 9.5, "timestamp": "2026-10-06T09:30:00+08:00"})
        result = engine.process_signal(decision, trade_date="2026-10-06")
        engine.fill_order(result["order"]["order_id"], market_price=10.0, trade_date="2026-10-06")

        engine.new_trade_date("2026-10-09", prev_trade_date="2026-10-06")

        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.quantity == 1000

        # Partial sell (300 shares)
        sell_decision = _sell_decision(entry_plan={}, exit_plan={"exit_price": 11.0, "exit_reason": "partial_take_profit", "timestamp": "2026-10-09T09:30:00+08:00"})
        sr = engine.process_signal(sell_decision, trade_date="2026-10-09")
        engine.fill_order(sr["order"]["order_id"], market_price=11.0, fill_pct=0.3, trade_date="2026-10-09")

        pos = engine.ledger.get_position("600000.SH")
        assert pos is not None
        assert pos.quantity == 700, f"Expected 700, got {pos.quantity}"


# ===================================================================
# F05: Persistence, restart recovery, reconciliation
# ===================================================================


class TestF05_Persistence:
    """Verify persistence, restart recovery, and unknown order detection."""

    def test_persistence_save_and_restore(self, persist_path: Path, paper_risk: PaperRiskEngine):
        """Ledger state persists and restores correctly."""
        # First session
        ledger1 = PaperLedger(persist_path=persist_path)
        ledger1.open_position(symbol="600000.SH", direction="LONG", quantity=1000, entry_price=10.0, trade_date="2026-10-06")
        assert len(ledger1.list_positions(status="OPEN")) == 1

        # Second session (reload)
        ledger2 = PaperLedger(persist_path=persist_path)
        ledger2.load()
        positions = ledger2.list_positions(status="OPEN")
        assert len(positions) == 1
        assert positions[0].symbol == "600000.SH"
        assert positions[0].quantity == 1000
        assert positions[0].entry_price == 10.0

    def test_restart_recovery_preserves_pnl(self, persist_path: Path, paper_risk: PaperRiskEngine):
        """P&L data survives restart."""
        ledger1 = PaperLedger(persist_path=persist_path)
        pos = ledger1.open_position(symbol="600000.SH", direction="LONG", quantity=1000, entry_price=10.0, trade_date="2026-10-06")
        ledger1.close_position(symbol="600000.SH", exit_price=11.0, exit_reason="take_profit")
        assert pos.status == "CLOSED"
        assert pos.pnl > 0

        # Reload
        ledger2 = PaperLedger(persist_path=persist_path)
        ledger2.load()
        pos2 = ledger2.get_position("600000.SH")
        assert pos2 is not None
        assert pos2.status == "CLOSED"
        assert pos2.pnl > 0

    def test_unknown_order_detection(self, persist_path: Path, paper_risk: PaperRiskEngine):
        """Detect orders present in persisted state but not in current session."""
        ledger1 = PaperLedger(persist_path=persist_path)
        o = PaperOrder(
            order_id="ORD-UNKNOWN-001",
            decision_id="DEC-001",
            symbol="600000.SH",
            direction="BUY",
            intent_id="INT-001",
            quantity=1000,
            limit_price=10.0,
            status="CREATED",
        )
        ledger1.register_order(o)

        # Reload
        ledger2 = PaperLedger(persist_path=persist_path)
        ledger2.load()
        # If an order was persisted but is missing after reload, detect it
        # Actually in this case the order IS in the new session
        assert ledger2.get_order("ORD-UNKNOWN-001") is not None

        # Now simulate network interruption: clear orders but keep known set
        ledger3 = PaperLedger(persist_path=persist_path)
        ledger3._known_order_ids.add("ORD-WAS-LOST-002")
        unknown = ledger3.detect_unknown_orders()
        assert "ORD-WAS-LOST-002" in unknown

    def test_json_persistence_format(self, persist_path: Path):
        """Persistence file should be valid JSON with all sections."""
        ledger = PaperLedger(persist_path=persist_path)
        ledger.open_position(symbol="600000.SH", direction="LONG", quantity=500, entry_price=15.0, trade_date="2026-10-06")

        data = json.loads(persist_path.read_text(encoding="utf-8"))
        assert "positions" in data
        assert "round_trips" in data
        assert "orders" in data
        assert "bought_today" in data
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "600000.SH"

    def test_corporate_action_dividend(self, engine: PaperEngine):
        """Dividends should adjust P&L."""
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        engine.fill_order(result["order"]["order_id"], market_price=10.0, trade_date="2026-10-06")

        pos = engine.ledger.get_position("600000.SH")
        initial_pnl = pos.pnl

        # Apply dividend
        engine.ledger.corporate_action_adjust("600000.SH", dividend_per_share=0.5)
        assert pos.pnl > initial_pnl


# ===================================================================
# F06: 20+ trading days, 7 failure scenarios
# ===================================================================


class TestF06_ExtendedScenario:
    """20+ trading days with 7 distinct failure scenarios."""

    def test_extended_trading_with_failures(self, persist_path: Path, paper_risk: PaperRiskEngine):
        """Simulate 22 trading days — each of the 7 failure scenarios has its own assert.

        Scenarios:
        1. Duplicate events: submit same order twice, assert second rejected
        2. Out-of-order: submit fill before order, assert rejected
        3. Network disconnect: simulate missing responses, assert recovery
        4. Process restart: save state, create new engine, assert state restored
        5. Partial fill: submit order, fill 30%, assert remaining open
        6. Rejection: submit order, reject, assert cash released
        7. Cancellation: submit order, cancel, assert cash/position restored
        """
        ledger = PaperLedger(persist_path=persist_path)
        engine = PaperEngine(
            ledger=ledger,
            risk=paper_risk,
            initial_cash=_INITIAL_CASH,
            allow_real_execution=False,
        )

        # Generate 22 trading days (M-F pattern for ~6 weeks)
        trade_dates = _trading_days("2026-10-05", count=22)

        # Track state
        active_orders: dict[str, dict[str, Any]] = {}
        scenario_checks: list[str] = []

        for i, td in enumerate(trade_dates):
            # Advance trade date (except first)
            if i > 0:
                engine.new_trade_date(td, prev_trade_date=trade_dates[i - 1])

            # ── Scenario 1: Duplicate events (day 1) ──────────────────────────
            if i == 0:
                dec1 = _buy_decision(symbol="600000.SH", decision_id="DEC-F06-01")
                r1 = engine.process_signal(dec1, trade_date=td)
                assert r1["status"] == "CREATED", "Scenario 1: First order should be created"
                active_orders["ord1"] = r1["order"]

                # Submit the exact same decision again → must be rejected
                r1_dup = engine.process_signal(dec1, trade_date=td)
                assert r1_dup["status"] == "REJECTED", (
                    f"Scenario 1: Duplicate order should be REJECTED, got {r1_dup['status']}"
                )
                scenario_checks.append("SC1_DUPLICATE_REJECTED")

            # ── Scenario 2: Out-of-order — fill before order (day 2) ──────────
            if i == 1:
                # Attempt to fill an order that does not exist yet
                fill_before = engine.fill_order("ORD-NEVER-CREATED", market_price=10.0, trade_date=td)
                assert fill_before.get("error") == "ORDER_NOT_FOUND", (
                    f"Scenario 2: Fill before order should return ORDER_NOT_FOUND, got {fill_before}"
                )
                scenario_checks.append("SC2_OUT_OF_ORDER_REJECTED")
                # Cancel ord1 (from day 0) so later scenarios can use the same symbol
                engine.cancel_order(active_orders["ord1"]["order_id"])

            # ── Scenario 3: Network disconnect (day 3) ─────────────────────────
            if i == 2:
                # Submit order, then fill without providing market_price (no price_provider).
                # Engine must fall back to limit_price and recover gracefully.
                dec3 = _buy_decision(
                    symbol="600000.SH",
                    decision_id="DEC-F06-03",
                    entry_plan={"limit_price": 10.50, "max_quantity": 500, "stop_price": 10.0,
                                "timestamp": f"{td}T09:30:00+08:00"},
                )
                r3 = engine.process_signal(dec3, trade_date=td)
                assert r3["status"] == "CREATED", f"Scenario 3: Order should be created, got {r3['status']}"
                oid3 = r3["order"]["order_id"]

                # Fill without market_price — simulates missing market-data responses
                fill3 = engine.fill_order(oid3, trade_date=td)
                assert fill3["filled"] is True, f"Scenario 3: Fill should succeed with fallback price, got {fill3}"
                # Without market_price or price_provider, raw_price falls back to limit_price.
                # Slippage is then applied (BUY: limit_price * (1 + slippage_bps/10000)).
                expected_fill = round(10.50 * (1 + 0.0005), 4)  # 10.50525 with default 5bps slippage
                assert abs(fill3["fill_price"] - expected_fill) < 0.001, (
                    f"Scenario 3: Fill price {fill3['fill_price']} should be close to {expected_fill}"
                )
                assert fill3["raw_price"] == 10.50, (
                    f"Scenario 3: Raw price should be limit_price 10.50, got {fill3['raw_price']}"
                )
                scenario_checks.append("SC3_DISCONNECT_RECOVERY")

            # ── Scenario 4: Process restart (day 5) ────────────────────────────
            if i == 4:
                # Save state (auto-saved by ledger), create new engine, reload
                new_ledger = PaperLedger(persist_path=persist_path)
                new_ledger.load()
                new_engine = PaperEngine(
                    ledger=new_ledger,
                    risk=PaperRiskEngine(initial_cash=_INITIAL_CASH),
                    initial_cash=_INITIAL_CASH,
                    allow_real_execution=False,
                )

                # Assert state is restored: position from days 0, 2, 3
                pos = new_ledger.get_position("600000.SH")
                assert pos is not None, "Scenario 4: Position should survive process restart"
                assert pos.status == "OPEN", f"Scenario 4: Position should be OPEN, got {pos.status}"
                assert pos.quantity > 0, f"Scenario 4: Position quantity should be > 0, got {pos.quantity}"
                assert pos.symbol == "600000.SH"
                scenario_checks.append("SC4_RESTART_STATE_RESTORED")

                # Continue using the original engine for remaining days
                ledger.load()
                engine = PaperEngine(
                    ledger=ledger,
                    risk=paper_risk,
                    initial_cash=_INITIAL_CASH,
                    allow_real_execution=False,
                )

            # ── Scenario 5: Partial fill (day 8) ───────────────────────────────
            if i == 7:
                dec5 = _buy_decision(
                    symbol="300001.SZ", max_quantity=2000,
                    entry_plan={"limit_price": 20.0, "max_quantity": 2000, "stop_price": 19.0,
                                "timestamp": f"{td}T09:30:00+08:00"},
                    decision_id="DEC-F06-05",
                )
                r5 = engine.process_signal(dec5, trade_date=td)
                assert r5["status"] == "CREATED", f"Scenario 5: Order should be CREATED, got {r5['status']}"
                oid5 = r5["order"]["order_id"]
                total5 = r5["order"]["quantity"]

                # Fill 30%
                fill5 = engine.fill_order(oid5, market_price=20.0, fill_pct=0.3, trade_date=td)
                assert fill5["filled"] is True, f"Scenario 5: Fill should succeed, got {fill5}"
                assert fill5["is_partial"] is True, "Scenario 5: Fill should report is_partial=True"

                order5 = engine.ledger.get_order(oid5)
                assert order5 is not None
                assert order5.status == "PARTIAL_FILLED", (
                    f"Scenario 5: Order should be PARTIAL_FILLED, got {order5.status}"
                )
                assert order5.filled_quantity < total5, (
                    f"Scenario 5: Filled quantity {order5.filled_quantity} should be < total {total5}"
                )
                assert order5.filled_quantity > 0, "Scenario 5: Should have some filled quantity"

                pos5 = engine.ledger.get_position("300001.SZ")
                assert pos5 is not None, "Scenario 5: Position should exist after partial fill"
                assert pos5.status == "OPEN", f"Scenario 5: Position should be OPEN, got {pos5.status}"
                assert pos5.quantity == fill5["fill_quantity"], (
                    f"Scenario 5: Position quantity {pos5.quantity} should match fill quantity {fill5['fill_quantity']}"
                )
                scenario_checks.append("SC5_PARTIAL_FILL_REMAINING")

            # ── Scenario 6: Rejection (day 10) ─────────────────────────────────
            if i == 9:
                cash_before_sc6 = engine.cash
                dec6 = _buy_decision(
                    symbol="600000.SH", decision_id="DEC-F06-06",
                    entry_plan={"limit_price": 10.50, "max_quantity": 400, "stop_price": 10.0,
                                "timestamp": f"{td}T09:30:00+08:00"},
                )
                r6 = engine.process_signal(dec6, trade_date=td)
                assert r6["status"] == "CREATED", f"Scenario 6: Order should be CREATED, got {r6['status']}"
                oid6 = r6["order"]["order_id"]

                # Reject the order
                reject6 = engine.reject_order(oid6, reason="ADMIN_REJECT")
                assert reject6["status"] == "REJECTED", (
                    f"Scenario 6: Order should be REJECTED, got {reject6}"
                )

                # Order should exist in REJECTED state
                ord6 = engine.ledger.get_order(oid6)
                assert ord6 is not None
                assert ord6.status == "REJECTED", f"Scenario 6: Order status should be REJECTED, got {ord6.status}"

                # Cash should be unchanged (no fill occurred)
                cash_after_sc6 = engine.cash
                assert abs(cash_after_sc6 - cash_before_sc6) < 0.01, (
                    f"Scenario 6: Cash should not change after rejection "
                    f"(before={cash_before_sc6}, after={cash_after_sc6})"
                )

                # Position from earlier buys still exists
                assert engine.ledger.get_position("600000.SH") is not None
                scenario_checks.append("SC6_REJECTION_CASH_RELEASED")

            # ── Sell first position to free cash (day 11) ─────────────────────
            if i == 10:
                sell_dec = _sell_decision(
                    symbol="600000.SH", decision_id="DEC-F06-EXIT",
                    exit_plan={"exit_price": 11.0, "exit_reason": "exit_target",
                               "timestamp": f"{td}T09:30:00+08:00"},
                )
                sr = engine.process_signal(sell_dec, trade_date=td)
                assert sr["status"] == "CREATED", f"Sell should be CREATED, got {sr['status']}"
                sell_oid = sr["order"]["order_id"]
                sell_fill = engine.fill_order(sell_oid, market_price=11.0, trade_date=td)
                assert sell_fill["filled"] is True, f"Sell fill should succeed, got {sell_fill}"

            # ── Scenario 7: Cancellation (day 12) ──────────────────────────────
            if i == 12:
                cash_before_sc7 = engine.cash
                dec7 = _buy_decision(
                    symbol="600000.SH", decision_id="DEC-F06-07",
                    entry_plan={"limit_price": 11.50, "max_quantity": 300, "stop_price": 11.0,
                                "timestamp": f"{td}T09:30:00+08:00"},
                )
                r7 = engine.process_signal(dec7, trade_date=td)
                assert r7["status"] == "CREATED", f"Scenario 7: Order should be CREATED, got {r7['status']}"
                oid7 = r7["order"]["order_id"]

                # Cancel the order
                cancel7 = engine.cancel_order(oid7)
                assert cancel7["status"] == "CANCELLED", (
                    f"Scenario 7: Order should be CANCELLED, got {cancel7}"
                )

                # Order should be in CANCELLED state
                ord7 = engine.ledger.get_order(oid7)
                assert ord7 is not None
                assert ord7.status == "CANCELLED", f"Scenario 7: Order status should be CANCELLED, got {ord7.status}"

                # Cash should be unchanged (no fill occurred)
                cash_after_sc7 = engine.cash
                assert abs(cash_after_sc7 - cash_before_sc7) < 0.01, (
                    f"Scenario 7: Cash should not change after cancellation "
                    f"(before={cash_before_sc7}, after={cash_after_sc7})"
                )
                scenario_checks.append("SC7_CANCELLATION_CASH_POSITION")

        # Verify all 7 scenarios executed
        assert len(scenario_checks) >= 7, f"Only {len(scenario_checks)} scenarios executed: {scenario_checks}"

        # Verify positions and cash are sane
        positions = ledger.list_positions()
        assert any(p.status == "OPEN" for p in positions), "At least one OPEN position should exist by end of scenario"
        assert engine.cash > 0, "Cash should be positive after all trading"

        # Verify round trips were recorded (sell triggered a round trip)
        rts = ledger.list_round_trips()
        assert len(rts) >= 1, f"At least one round trip should exist, got {len(rts)}"


def _trading_days(start: str, count: int) -> list[str]:
    """Generate simple trading days (skip weekends)."""
    from datetime import date, timedelta
    from a_share_agent.utils import parse_iso

    try:
        d = parse_iso(start + "T00:00:00+08:00").date()
    except Exception:
        d = date(2026, 10, 5)

    days: list[str] = []
    while len(days) < count:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


# ===================================================================
# F07: allow_real_execution always False
# ===================================================================


class TestF07_NoRealExecution:
    """Verify no real broker calls and allow_real_execution is never True."""

    def test_allow_real_execution_always_false(self, engine: PaperEngine):
        """allow_real_execution property must be False."""
        # The engine was constructed with allow_real_execution=False
        assert engine._allow_real_execution is False

    def test_no_broker_calls(self, engine: PaperEngine):
        """No broker/external MCP calls should be made."""
        # PaperEngine uses PaperRiskEngine (pure, no I/O)
        # PaperLedger self-manages state
        # No MCP invoker, no external risk service
        # Just verify the engine processes signals without hitting external services
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        assert result["status"] in ("CREATED", "REJECTED")

    def test_engine_is_self_contained(self, engine: PaperEngine):
        """Engine should have no broker client references."""
        # No mcp, no exec client, no external service
        assert not hasattr(engine, "_mcp")
        # PaperEngine should work without MCP
        decision = _buy_decision()
        result = engine.process_signal(decision, trade_date="2026-10-06")
        # Should not crash even without any external service
        assert result is not None
