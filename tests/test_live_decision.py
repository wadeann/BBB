"""Tests for Phase 5 Live Decision Engine + Alert System (E01–E07).

Run with: pytest tests/test_live_decision.py -q
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from a_share_agent.strategy.decision_state import (
    DecisionState,
    DecisionStatus,
    SignalDecision,
    TransitionRecord,
    is_valid_transition,
)
from a_share_agent.strategy.decision_engine import (
    DecisionEngine,
    PolicyValidation,
)
from a_share_agent.notifications.alert_engine import AlertEngine
from a_share_agent.notifications.store import NotificationStore


# ════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_candidate() -> dict[str, Any]:
    return {
        "symbol": "600000.SH",
        "pattern_id": "ma60_breakout_retest",
        "pattern_version": "1.0.0",
        "score": 85.0,
        "score_breakdown": {
            "supply_demand_catalyst": 16.0,
            "sector_resonance": 18.0,
            "trend_structure": 17.0,
            "money_flow": 12.0,
            "volume_price_pattern": 13.0,
            "news_fundamentals": 9.0,
        },
        "regime_at_signal": "BULLISH",
        "theme_lifecycle": "EARLY_GROWTH",
        "sector": "银行",
        "sector_strength": "strong",
        "liquidity_score": 0.9,
        "rs_score": 0.8,
        "sector_resonance": 0.85,
        "entry_price": 10.25,
        "stop_price": 9.60,
        "evidence_summary": {"ma60_retest_confirm": True, "volume_above_ma20": True},
        "rank": 1,
    }


@pytest.fixture
def sample_context() -> dict[str, Any]:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "regime": "BULLISH",
        "theme_lifecycle": "EARLY_GROWTH",
        "feature_version": "0.8.0",
        "trade_date": "2026-10-08",
        "phase": "ENTRY_WINDOW_AM",
        "breadth": {"advancing": 2800, "declining": 1200, "ratio": 2.33},
        "leaders": [{"sector_name": "银行", "strength_score": 85}],
    }


@pytest.fixture
def valid_policy() -> dict[str, Any]:
    """A valid, non-expired PolicyEntry dict."""
    future = (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
    return {
        "status": "APPROVED",
        "expires_at": str(future),
        "evidence_id": "ev_abc123",
        "evidence_version": "sha256:xyz",
        "key": ("ma60_breakout_retest", "BULLISH", "EARLY_GROWTH", "1.0.0"),
        "valid_from": str(datetime.now(timezone.utc).timestamp()),
        "issued_at": str(datetime.now(timezone.utc).timestamp()),
    }


@pytest.fixture
def notify_store(tmp_path: Path) -> NotificationStore:
    return NotificationStore(str(tmp_path / "notifications_test.sqlite"))


@pytest.fixture
def alert_engine(notify_store: NotificationStore) -> AlertEngine:
    return AlertEngine(notify_store)


# ════════════════════════════════════════════════════════════════════════
# E01: Historical replay and live adapter produce same decision
# ════════════════════════════════════════════════════════════════════════


class TestE01HistoricalReplayParity:
    """Same inputs → same SignalDecision regardless of adapter mode."""

    def test_live_adapter_produces_deterministic_output(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        valid_policy: dict[str, Any],
    ):
        """Live DecisionEngine must produce a deterministic SignalDecision."""
        engine = DecisionEngine()
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
            phase="ENTRY_WINDOW_AM",
        )

        assert isinstance(decision, SignalDecision)
        assert decision.symbol == "600000.SH"
        assert decision.pattern_id == "ma60_breakout_retest"
        assert decision.status in (DecisionStatus.WATCH, DecisionStatus.BUY_READY)
        assert decision.created_at
        assert decision.state_version == 1

    def test_historical_replay_produces_identical_output(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        valid_policy: dict[str, Any],
    ):
        """Replaying the same input through a fresh engine yields same status."""
        # First pass
        engine1 = DecisionEngine()
        d1 = engine1.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
            phase="ENTRY_WINDOW_AM",
        )

        # Second pass — fresh engine, same inputs
        engine2 = DecisionEngine()
        d2 = engine2.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
            phase="ENTRY_WINDOW_AM",
        )

        assert d1.status == d2.status
        assert d1.symbol == d2.symbol
        assert d1.pattern_id == d2.pattern_id
        assert isinstance(d1.context, dict)
        assert isinstance(d2.context, dict)

    def test_replay_via_serialized_state(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        valid_policy: dict[str, Any],
    ):
        """State serialization round-trip preserves decision continuity."""
        engine = DecisionEngine()
        d1 = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
        )
        assert engine.state is not None

        # Serialize and restore
        state_dict = engine.state.to_dict()
        restored = DecisionState.from_dict(state_dict)

        engine2 = DecisionEngine(state=restored)
        d2 = engine2.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_002",
            event_seq=2,
        )

        assert d2.symbol == d1.symbol
        assert d2.status == d1.status or d2.status == DecisionStatus.BUY_READY


# ════════════════════════════════════════════════════════════════════════
# E02: Bar order handling
# ════════════════════════════════════════════════════════════════════════


class TestE02BarOrderHandling:
    """Unfinished, repeat, late, and out-of-order bars."""

    def test_repeat_event_id_rejected(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
    ):
        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_id="bar_001",
            event_seq=1,
        )

        # Same event_id again
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_id="bar_001",
            event_seq=2,
        )
        assert any(
            a["alert_type"] == "BAR_ORDER_WARNING"
            for a in decision.alerts
        )

    def test_out_of_order_seq_rejected(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
    ):
        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=10,
        )

        # Lower seq number
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=5,
        )
        assert any(
            a["alert_type"] == "BAR_ORDER_WARNING"
            for a in decision.alerts
        )

    def test_repeat_seq_zero_ok(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
    ):
        """seq=0 is unset and should not trigger ordering checks."""
        engine = DecisionEngine()
        d1 = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=0,
        )
        d2 = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=0,
        )
        # No bar-order warning
        warnings = [
            a for a in d2.alerts
            if a["alert_type"] == "BAR_ORDER_WARNING"
        ]
        assert len(warnings) == 0

    def test_late_bar_has_warning(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
    ):
        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_id="bar_001",
            event_seq=1,
        )
        engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_id="bar_002",
            event_seq=10,  # jump ahead
        )

        # Now a late bar
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_id="bar_003",
            event_seq=3,  # earlier than last seen
        )
        assert any(
            a["alert_type"] == "BAR_ORDER_WARNING"
            for a in decision.alerts
        )

    def test_order_check_does_not_block_exit(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
    ):
        """Out-of-order bars should still produce a decision, not crash."""
        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=5,
        )
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            event_seq=2,  # out of order
        )
        assert isinstance(decision, SignalDecision)
        assert decision.symbol == "600000.SH"


# ════════════════════════════════════════════════════════════════════════
# E03: State transitions
# ════════════════════════════════════════════════════════════════════════


class TestE03StateTransitions:
    """All legal transitions + illegal transitions blocked + audit."""

    def test_legal_watch_to_buy_ready(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        assert state.status == DecisionStatus.WATCH

        record = state.mark_buy_ready(reason="score_above_70")
        assert state.status == DecisionStatus.BUY_READY
        assert record.from_status == DecisionStatus.WATCH
        assert record.to_status == DecisionStatus.BUY_READY
        assert len(state.audit_trail) == 1

    def test_legal_full_chain(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        state.mark_buy_ready()
        state.mark_buy_triggered()
        state.mark_hold()
        state.mark_sell_warning()
        state.mark_sell_triggered()
        assert state.status == DecisionStatus.SELL_TRIGGERED
        assert len(state.audit_trail) == 5

    def test_legal_invalidate_anywhere(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        state.mark_buy_ready()
        state.invalidate(reason="market_regime_changed")
        assert state.status == DecisionStatus.INVALIDATED

        from_hold = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        from_hold.status = DecisionStatus.HOLD
        from_hold.invalidate()
        assert from_hold.status == DecisionStatus.INVALIDATED

    def test_illegal_transition_raises(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        # Cannot go from WATCH to HOLD (skip BUY_READY and BUY_TRIGGERED)
        with pytest.raises(ValueError, match="Illegal transition"):
            state.transition(DecisionStatus.HOLD)

    def test_illegal_skip_buy_ready(self):
        """WATCH → BUY_TRIGGERED is illegal — must go through BUY_READY."""
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        with pytest.raises(ValueError, match="Illegal transition"):
            state.mark_buy_triggered()

    def test_illegal_sell_triggered_hold(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        state.mark_buy_ready()
        state.mark_buy_triggered()
        state.mark_hold()
        state.mark_sell_warning()
        state.mark_sell_triggered()
        with pytest.raises(ValueError, match="Illegal transition"):
            state.mark_hold()


    def test_audit_trail_records_all(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        state.mark_buy_ready(reason="score=85")
        state.mark_buy_triggered(reason="trigger_buy=True")
        state.mark_hold(reason="filled")
        assert len(state.audit_trail) == 3
        assert state.audit_trail[0].reason == "score=85"
        assert state.audit_trail[1].from_status == DecisionStatus.BUY_READY
        assert state.audit_trail[2].to_status == DecisionStatus.HOLD

    def test_audit_trail_serializes(self):
        state = DecisionState("600000.SH", "ma60_breakout_retest", "1.0.0")
        state.mark_buy_ready()
        d = state.to_dict()
        assert d["symbol"] == "600000.SH"
        assert len(d["audit_trail"]) == 1
        assert d["audit_trail"][0]["to"] == "BUY_READY"

        restored = DecisionState.from_dict(d)
        assert restored.status == DecisionStatus.BUY_READY
        assert len(restored.audit_trail) == 1

    def test_is_valid_transition_function(self):
        assert is_valid_transition(DecisionStatus.WATCH, DecisionStatus.BUY_READY)
        assert is_valid_transition(DecisionStatus.WATCH, DecisionStatus.INVALIDATED)
        assert not is_valid_transition(DecisionStatus.WATCH, DecisionStatus.HOLD)
        assert not is_valid_transition(DecisionStatus.SELL_TRIGGERED, DecisionStatus.WATCH)


# ════════════════════════════════════════════════════════════════════════
# E04: Alert correctness
# ════════════════════════════════════════════════════════════════════════


class TestE04AlertCorrectness:
    """Alerts must carry timestamp/as_of/Pattern/Context/evidence."""

    def test_alert_has_all_required_fields(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        alert_engine: AlertEngine,
    ):
        alerts = alert_engine.emit_alerts(
            decision_id="dec_001",
            status="WATCH",
            context=sample_context,
            candidate=sample_candidate,
            reason="pattern_detected",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        assert len(alerts) >= 1
        alert = alerts[0]

        # Required fields from spec
        assert "timestamp" in alert
        assert "as_of" in alert
        assert alert["as_of"] == sample_context["as_of"]
        assert "pattern" in alert
        assert alert["pattern"]["pattern_id"] == "ma60_breakout_retest"
        assert alert["pattern"]["pattern_version"] == "1.0.0"
        assert "context" in alert
        assert alert["context"]["regime"] == "BULLISH"
        assert alert["context"]["theme_lifecycle"] == "EARLY_GROWTH"

    def test_alert_contains_oos_evidence(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        alert_engine: AlertEngine,
    ):
        alerts = alert_engine.emit_alerts(
            decision_id="dec_002",
            status="BUY_READY",
            context=sample_context,
            candidate=sample_candidate,
            reason="conditions_met",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        alert = alerts[0]
        # evidence field should reference the candidate's evidence_summary
        assert "evidence" in alert
        assert alert["evidence"].get("ma60_retest_confirm") is True
        assert alert["evidence"].get("volume_above_ma20") is True

    def test_alert_has_entry_and_stop_info(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        alert_engine: AlertEngine,
    ):
        alerts = alert_engine.emit_alerts(
            decision_id="dec_003",
            status="BUY_TRIGGERED",
            context=sample_context,
            candidate=sample_candidate,
            reason="intent_created",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        alert = alerts[0]
        assert alert["entry_info"]["entry_price"] == 10.25
        assert alert["entry_info"]["stop_price"] == 9.60

    def test_alert_idempotent(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        alert_engine: AlertEngine,
    ):
        alerts1 = alert_engine.emit_alerts(
            decision_id="dec_idempotent",
            status="WATCH",
            context=sample_context,
            candidate=sample_candidate,
            reason="first",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        assert len(alerts1) >= 1

        # Same decision_id — should be skipped
        alerts2 = alert_engine.emit_alerts(
            decision_id="dec_idempotent",
            status="BUY_READY",
            context=sample_context,
            candidate=sample_candidate,
            reason="second",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        assert len(alerts2) == 0

    def test_alert_types_for_all_statuses(self):
        """Verify each DecisionStatus maps to the expected alert type."""
        from a_share_agent.notifications.alert_engine import AlertEngine

        mapping = {
            "WATCH": "WATCH_ADDED",
            "BUY_READY": "BUY_READY",
            "BUY_TRIGGERED": "BUY_TRIGGERED",
            "HOLD": "HOLD_ENTERED",
            "SELL_WARNING": "SELL_WARNING",
            "SELL_TRIGGERED": "SELL_TRIGGERED",
            "INVALIDATED": "INVALIDATED",
        }
        for status, expected in mapping.items():
            assert AlertEngine._alert_type_for(status) == expected


# ════════════════════════════════════════════════════════════════════════
# E05: Stale data handling
# ════════════════════════════════════════════════════════════════════════


class TestE05StaleData:
    """Stale data blocks new buys, logs alert for existing positions."""

    def test_stale_context_blocks_new_entry(
        self,
        sample_candidate: dict[str, Any],
        valid_policy: dict[str, Any],
    ):
        """Context older than 30 minutes should block entry."""
        fresh = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "regime": "BULLISH",
            "theme_lifecycle": "EARLY_GROWTH",
        }
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        old_context = {
            "as_of": old_time,
            "regime": "BULLISH",
            "theme_lifecycle": "EARLY_GROWTH",
        }

        # First call with fresh context to establish state
        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=fresh,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
        )

        # Second call with stale context — should block new entry
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=old_context,
            policy=valid_policy,
            event_id="bar_002",
            event_seq=2,
        )

        assert any(a["alert_type"] == "STALE_DATA" for a in decision.alerts)

    def test_fresh_context_allows_entry(
        self,
        sample_candidate: dict[str, Any],
        sample_context: dict[str, Any],
        valid_policy: dict[str, Any],
    ):
        """Fresh context (within 30 min) should allow entry."""
        engine = DecisionEngine()
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=sample_context,
            policy=valid_policy,
            event_id="bar_001",
            event_seq=1,
        )

        stale_alerts = [a for a in decision.alerts if a["alert_type"] == "STALE_DATA"]
        assert len(stale_alerts) == 0

    def test_stale_does_not_block_exit(
        self,
        sample_candidate: dict[str, Any],
    ):
        """Stale data should NOT block exit transitions."""
        # First, set up a HOLD state
        ctx_fresh = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "regime": "BULLISH",
        }

        engine = DecisionEngine()
        engine.evaluate(
            candidate=sample_candidate,
            context=ctx_fresh,
            event_id="bar_001",
            event_seq=1,
        )
        # Trigger buy
        engine.evaluate(
            candidate=sample_candidate,
            context=ctx_fresh | {"trigger_buy": True},
            event_id="bar_002",
            event_seq=2,
        )
        # Fill position → HOLD
        engine.evaluate(
            candidate=sample_candidate,
            context=ctx_fresh | {"trigger_buy": True, "position_filled": True},
            event_id="bar_003",
            event_seq=3,
        )
        # Fourth call: BUY_TRIGGERED → HOLD (position_filled=True)
        engine.evaluate(
            candidate=sample_candidate,
            context=ctx_fresh | {"position_filled": True},
            event_id="bar_004",
            event_seq=4,
        )
        assert engine.state.status == DecisionStatus.HOLD

        # Now stale context — should still allow exit warning
        old_ctx = {
            "as_of": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "exit_signal": "warning",
        }
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=old_ctx,
            event_id="bar_004",
            event_seq=4,
        )
        # Should still produce a decision without blocking
        assert isinstance(decision, SignalDecision)

    def test_stale_data_alert_logged(
        self,
        sample_candidate: dict[str, Any],
        alert_engine: AlertEngine,
    ):
        old_ctx = {
            "as_of": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "regime": "BULLISH",
        }
        alerts = alert_engine.emit_alerts(
            decision_id="dec_stale",
            status="STALE_DATA",
            context=old_ctx,
            candidate=sample_candidate,
            reason="Stale context — entry blocked",
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
        )
        assert any(a["alert_type"] == "STALE_DATA" for a in alerts)


# ════════════════════════════════════════════════════════════════════════
# E06: Read-only market access smoke (no real credentials)
# ════════════════════════════════════════════════════════════════════════


class TestE06ReadOnlyMarketAccess:
    """Demonstrate that the decision engine works without real MCP credentials."""

    def test_engine_no_mcp_required(
        self,
        sample_candidate: dict[str, Any],
    ):
        """The DecisionEngine works entirely from in-memory data — no MCP needed."""
        ctx = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "regime": "BULLISH",
            "theme_lifecycle": "EARLY_GROWTH",
            "feature_version": "0.8.0",
        }
        engine = DecisionEngine()
        decision = engine.evaluate(
            candidate=sample_candidate,
            context=ctx,
        )
        assert isinstance(decision, SignalDecision)
        assert decision.symbol == "600000.SH"

    def test_candidate_construction_no_external_deps(self):
        """Candidate dicts are plain data — no external dependencies."""
        cand = {
            "symbol": "000001.SZ",
            "pattern_id": "triple_golden_cross",
            "pattern_version": "2.0.0",
            "score": 78.5,
            "score_breakdown": {"trend_structure": 18.0, "volume_price_pattern": 14.0},
            "regime_at_signal": "NEUTRAL",
            "theme_lifecycle": "MID_CYCLE",
            "sector": "科技",
            "sector_strength": "strong",
            "liquidity_score": 0.85,
            "rs_score": 0.75,
            "sector_resonance": 0.8,
            "entry_price": 15.50,
            "stop_price": 14.20,
            "evidence_summary": {"golden_cross_confirm": True},
            "rank": 3,
        }
        ctx = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "regime": "NEUTRAL",
            "theme_lifecycle": "MID_CYCLE",
        }
        engine = DecisionEngine()
        decision = engine.evaluate(candidate=cand, context=ctx)
        assert decision.symbol == "000001.SZ"
        assert decision.pattern_id == "triple_golden_cross"

    def test_signal_decision_serializes(self, sample_candidate: dict[str, Any]):
        """SignalDecision.to_dict() produces JSON-safe output."""
        ctx = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "regime": "BULLISH",
        }
        engine = DecisionEngine()
        decision = engine.evaluate(candidate=sample_candidate, context=ctx)

        d = decision.to_dict()
        assert isinstance(d, dict)
        assert d["symbol"] == "600000.SH"
        assert d["status"] in ("WATCH", "BUY_READY")
        # Verify JSON-serializable
        json.dumps(d)


# ════════════════════════════════════════════════════════════════════════
# E07: Scheduler phase consistency with PhasePermissions
# ════════════════════════════════════════════════════════════════════════


class TestE07SchedulerPhasePermissions:
    """Decision engine behavior must be consistent with PhasePermissions."""

    @pytest.fixture
    def permissions(self) -> Any:
        """Load real PhasePermissions from config."""
        from pathlib import Path
        import yaml

        cfg_path = Path(__file__).resolve().parents[1] / "config" / "phase_permissions.yaml"
        raw = yaml.safe_load(cfg_path.read_text())
        from a_share_agent.core.permissions import PhasePermissions
        return PhasePermissions(raw)

    @pytest.fixture
    def scheduler(self) -> Any:
        """Load real Scheduler from config."""
        from pathlib import Path
        import yaml
        from a_share_agent.core.scheduler import Scheduler
        from a_share_agent.core.permissions import PhasePermissions

        cfg_path = Path(__file__).resolve().parents[1] / "config" / "schedule.yaml"
        schedule = yaml.safe_load(cfg_path.read_text())

        perm_path = Path(__file__).resolve().parents[1] / "config" / "phase_permissions.yaml"
        perm_raw = yaml.safe_load(perm_path.read_text())
        perms = PhasePermissions(perm_raw)

        return Scheduler(schedule, perms)

    def test_preopen_phases_block_new_entry(self, permissions: Any):
        """PREOPEN_PRECHECK and PREOPEN_CONTEXT forbid new_entry."""
        for phase in ("PREOPEN_PRECHECK", "PREOPEN_CONTEXT"):
            p = permissions.for_phase(phase)
            assert p.get("new_entry") is False, f"{phase} should block new entry"

    def test_entry_windows_allow_new_entry(self, permissions: Any):
        """ENTRY_WINDOW_AM and ENTRY_WINDOW_PM allow new_entry."""
        for phase in ("ENTRY_WINDOW_AM", "ENTRY_WINDOW_PM"):
            p = permissions.for_phase(phase)
            assert p.get("new_entry") is True, f"{phase} should allow new entry"

    def test_close_phases_block_all_trading(self, permissions: Any):
        """CLOSE_AUCTION_FREEZE and CLOSE_RECONCILE block all actions."""
        for phase in ("CLOSE_AUCTION_FREEZE", "CLOSE_RECONCILE"):
            p = permissions.for_phase(phase)
            assert p.get("new_entry") is False
            assert p.get("place_order") is False
            assert p.get("cancel_order") is False
            assert p.get("reduce_position") is False

    def test_midday_review_blocks_all(self, permissions: Any):
        """MIDDAY_REVIEW forbids all order actions."""
        p = permissions.for_phase("MIDDAY_REVIEW")
        assert p.get("new_entry") is False
        assert p.get("place_order") is False
        assert p.get("cancel_order") is False
        assert p.get("reduce_position") is False

    def test_scheduler_returns_correct_phase(self, scheduler: Any):
        """Scheduler.phase_at() returns the expected phase for a given time."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")

        # Pre-open
        dt = datetime(2026, 10, 8, 8, 50, tzinfo=tz)
        assert scheduler.phase_at(dt) == "PREOPEN_PRECHECK"

        # Entry AM
        dt = datetime(2026, 10, 8, 9, 45, tzinfo=tz)
        assert scheduler.phase_at(dt) == "ENTRY_WINDOW_AM"

        # Morning monitor
        dt = datetime(2026, 10, 8, 10, 35, tzinfo=tz)
        assert scheduler.phase_at(dt) == "MORNING_MONITOR"

        # Midday
        dt = datetime(2026, 10, 8, 11, 45, tzinfo=tz)
        assert scheduler.phase_at(dt) == "MIDDAY_REVIEW"

        # Entry PM
        dt = datetime(2026, 10, 8, 13, 30, tzinfo=tz)
        assert scheduler.phase_at(dt) == "ENTRY_WINDOW_PM"

        # Close auction freeze
        dt = datetime(2026, 10, 8, 14, 58, tzinfo=tz)
        assert scheduler.phase_at(dt) == "CLOSE_AUCTION_FREEZE"

        # Close reconcile
        dt = datetime(2026, 10, 8, 15, 5, tzinfo=tz)
        assert scheduler.phase_at(dt) == "CLOSE_RECONCILE"

    def test_decision_engine_respects_phase(
        self,
        sample_candidate: dict[str, Any],
        permissions: Any,
    ):
        """DecisionEngine should be able to run in any phase — phase permissions
        govern execution, not the engine's ability to produce decisions."""
        for phase in (
            "PREOPEN_PRECHECK", "PREOPEN_CONTEXT",
            "ENTRY_WINDOW_AM", "MORNING_MONITOR",
            "MIDDAY_REVIEW", "ENTRY_WINDOW_PM",
            "CLOSE_AUCTION_FREEZE", "CLOSE_RECONCILE",
        ):
            ctx = {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "regime": "BULLISH",
                "phase": phase,
            }
            engine = DecisionEngine()
            try:
                decision = engine.evaluate(
                    candidate=sample_candidate,
                    context=ctx,
                    phase=phase,
                )
                assert isinstance(decision, SignalDecision)
                assert decision.symbol == "600000.SH"
            except Exception as exc:
                pytest.fail(f"Engine failed in phase {phase}: {exc}")
