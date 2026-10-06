"""Phase 5: Live Decision Engine.

Orchestrates stateful decision-making from Candidate + ContextSnapshot
to SignalDecision with alert payloads.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .decision_state import (
    DecisionState,
    DecisionStatus,
    SignalDecision,
    is_valid_transition,
)

# ── Policy validation result ──────────────────────────────────────────


@dataclass
class PolicyValidation:
    enabled: bool
    not_expired: bool
    evidence_ok: bool
    reason: str = ""


# ── Decision Engine ───────────────────────────────────────────────────


class DecisionEngine:
    """Orchestrate stateful decision-making for a single symbol.

    Responsibilities:
    - Accept Candidate + ContextSnapshot
    - Validate against PolicyEntry
    - Check event ordering (no repeats, out-of-order, late bars)
    - Run state machine transition
    - Produce SignalDecision with alert payload
    - Freeze decision-time context (no retroactive updates)
    """

    def __init__(self, state: DecisionState | None = None) -> None:
        self.state = state

    # ── High-level entry point ────────────────────────────────────────

    def evaluate(self, *,
                 candidate: dict[str, Any],
                 context: dict[str, Any],
                 policy: dict[str, Any] | None = None,
                 event_id: str | None = None,
                 event_seq: int = 0,
                 phase: str = "",
                 ) -> SignalDecision:
        """Run one decision cycle.

        Parameters
        ----------
        candidate : dict
            Serialized ``Candidate`` from P4b (or None for exit-only transitions).
        context : dict
            ``ContextSnapshot`` — market context at decision time.
        policy : dict | None
            Optional ``PolicyEntry`` for authorization checks.
        event_id : str | None
            Unique bar/event identifier for ordering checks.
        event_seq : int
            Monotonic sequence number for ordering checks.
        phase : str
            Current trading phase (for alert context).

        Returns
        -------
        SignalDecision with the result.
        """
        # 1. Freeze context at decision time
        frozen_context = dict(context)

        # 2. If no active state, start fresh
        if self.state is None or self.state.is_terminal:
            if candidate is None:
                raise ValueError("Cannot start decision engine without a candidate")
            self.state = DecisionState(
                symbol=candidate["symbol"],
                pattern_id=candidate["pattern_id"],
                pattern_version=candidate["pattern_version"],
            )
            decision = self._build_decision(
                status=DecisionStatus.WATCH,
                context=frozen_context,
                candidate=candidate,
                reason="pattern_detected_starting_watch",
                event_id=event_id,
            )
            self.state.last_event_id = event_id
            self.state.last_event_seq = event_seq
            return decision

        # 3. Check event ordering
        if event_id or event_seq > 0:
            try:
                self.state.check_event_order(event_id, event_seq)
            except ValueError as exc:
                # Out-of-order or repeat bar: produce warning, stay at current state
                decision = self._build_decision(
                    status=self.state.status,
                    context=frozen_context,
                    candidate=candidate,
                    reason=f"bar_order_violation:{exc}",
                    event_id=event_id,
                )
                decision.alerts.append({
                    "alert_type": "BAR_ORDER_WARNING",
                    "message": str(exc),
                    "as_of": frozen_context.get("as_of", _now_iso()),
                    "evidence_ref": event_id,
                })
                return decision

        # 4. Stale-data check: block new entries (buys) only
        if self.state.can_enter and self._is_stale_context(frozen_context):
            decision = self._build_decision(
                status=self.state.status,
                context=frozen_context,
                candidate=candidate,
                reason="stale_context_blocks_new_entry",
                event_id=event_id,
            )
            decision.alerts.append({
                "alert_type": "STALE_DATA",
                "message": "Stale context data prevents new entry",
                "as_of": frozen_context.get("as_of", _now_iso()),
                "evidence_ref": event_id,
            })
            return decision

        # 5. Policy check for new entries (buys)
        if self.state.can_enter and policy is not None:
            pv = self._validate_policy(policy)
            if not (pv.enabled and pv.not_expired and pv.evidence_ok):
                decision = self._build_decision(
                    status=self.state.status,
                    context=frozen_context,
                    candidate=candidate,
                    reason=f"policy_blocked:{pv.reason}",
                    event_id=event_id,
                )
                decision.alerts.append({
                    "alert_type": "POLICY_BLOCKED",
                    "message": pv.reason,
                    "as_of": frozen_context.get("as_of", _now_iso()),
                    "evidence_ref": event_id,
                })
                return decision

        # 6. Determine target status based on candidate + context signals
        target = self._determine_target_status(frozen_context, candidate)

        # 7. Execute state transition (or stay at current state)
        if target is not None and target != self.state.status:
            try:
                self.state.transition(
                    target,
                    reason=f"engine:{self._transition_reason(target)}",
                    trigger="engine",
                    event_id=event_id,
                    event_seq=event_seq,
                )
            except ValueError as exc:
                # Illegal transition: produce decision with current status
                decision = self._build_decision(
                    status=self.state.status,
                    context=frozen_context,
                    candidate=candidate,
                    reason=f"illegal_transition:{exc}",
                    event_id=event_id,
                )
                return decision

        # 8. Build SignalDecision with alert payloads
        decision = self._build_decision(
            status=self.state.status,
            context=frozen_context,
            candidate=candidate,
            reason=f"state={self.state.status.value}",
            event_id=event_id,
        )

        # Add transition-specific alerts
        alert_type = self._alert_type_for(self.state.status)
        decision.alerts.append({
            "alert_type": alert_type,
            "message": self._alert_message(self.state.status, candidate),
            "as_of": frozen_context.get("as_of", _now_iso()),
            "evidence_ref": candidate.get("evidence_summary", {}) if candidate else {},
        })

        # 9. Build entry/exit plans as appropriate
        if self.state.status == DecisionStatus.BUY_TRIGGERED:
            decision.entry_plan = {
                "limit_price": candidate.get("entry_price") if candidate else None,
                "max_quantity": 0,  # placeholder — set by risk engine
                "stop_price": candidate.get("stop_price") if candidate else None,
                "timestamp": _now_iso(),
            }
        elif self.state.status == DecisionStatus.HOLD:
            decision.entry_plan = {
                "limit_price": candidate.get("entry_price") if candidate else None,
                "stop_price": candidate.get("stop_price") if candidate else None,
                "timestamp": _now_iso(),
            }
        elif self.state.status == DecisionStatus.SELL_TRIGGERED:
            decision.exit_plan = {
                "exit_reason": "exit_signal_executed",
                "exit_price": candidate.get("stop_price") if candidate else None,
                "timestamp": _now_iso(),
            }
        elif self.state.status == DecisionStatus.SELL_WARNING:
            decision.exit_plan = {
                "exit_reason": "exit_signal_nearing",
                "exit_price": None,
                "timestamp": _now_iso(),
            }

        return decision

    # ── Policy validation ─────────────────────────────────────────────

    def _validate_policy(self, policy: dict[str, Any]) -> PolicyValidation:
        """Check a PolicyEntry dict for enabled/expired/evidence status."""
        if not policy:
            return PolicyValidation(False, False, False, "no_policy")

        status = str(policy.get("status", "")).lower()
        enabled = status in ("approved", "pending")
        not_expired = not self._is_expired(policy)
        evidence_ok = bool(policy.get("evidence_id"))

        reasons = []
        if not enabled:
            reasons.append(f"policy_status={status}")
        if not not_expired:
            reasons.append("expired")
        if not evidence_ok:
            reasons.append("missing_evidence")

        return PolicyValidation(
            enabled=enabled,
            not_expired=not_expired,
            evidence_ok=evidence_ok,
            reason="; ".join(reasons) if reasons else "ok",
        )

    @staticmethod
    def _is_expired(policy: dict[str, Any]) -> bool:
        expires = policy.get("expires_at")
        if expires is None:
            return False
        now = time.time()
        try:
            return now > float(expires)
        except (TypeError, ValueError):
            return True

    # ── Stale data detection ──────────────────────────────────────────

    @staticmethod
    def _is_stale_context(context: dict[str, Any]) -> bool:
        """Return True if context data is stale (blocks new entries only)."""
        as_of = context.get("as_of")
        if as_of is None:
            return False
        try:
            if isinstance(as_of, str):
                dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            else:
                return False
        except (ValueError, TypeError):
            return False
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        return age_seconds > 1800  # 30 min stale threshold

    # ── Target status determination ───────────────────────────────────

    def _determine_target_status(self, context: dict[str, Any],
                                 candidate: dict[str, Any] | None) -> DecisionStatus | None:
        """Determine target status from candidate + context signals.

        Returns None to stay at current state.
        """
        if candidate is None:
            return None

        current = self.state.status

        # Terminal states: no further transitions
        if current in (DecisionStatus.INVALIDATED, DecisionStatus.SELL_TRIGGERED):
            return None

        # WATCH → BUY_READY: score threshold met
        if current == DecisionStatus.WATCH:
            score = candidate.get("score", 0.0)
            if score >= 70:
                return DecisionStatus.BUY_READY
            return None

        # BUY_READY → BUY_TRIGGERED: explicit trigger signal
        if current == DecisionStatus.BUY_READY:
            if context.get("trigger_buy", False) is True:
                return DecisionStatus.BUY_TRIGGERED
            return None

        # BUY_TRIGGERED → HOLD: fill confirmed
        if current == DecisionStatus.BUY_TRIGGERED:
            if context.get("position_filled", False) is True:
                return DecisionStatus.HOLD
            return None

        # HOLD → SELL_WARNING/SELL_TRIGGERED
        if current == DecisionStatus.HOLD:
            exit_signal = context.get("exit_signal", "")
            if exit_signal == "triggered":
                return DecisionStatus.SELL_TRIGGERED
            if exit_signal == "warning":
                return DecisionStatus.SELL_WARNING
            return None

        # SELL_WARNING → SELL_TRIGGERED
        if current == DecisionStatus.SELL_WARNING:
            if context.get("exit_signal", "") in ("triggered", "executed"):
                return DecisionStatus.SELL_TRIGGERED
            return None

        return None

    # ── Alert helpers ─────────────────────────────────────────────────

    @staticmethod
    def _alert_type_for(status: DecisionStatus) -> str:
        mapping = {
            DecisionStatus.WATCH: "WATCH_ADDED",
            DecisionStatus.BUY_READY: "BUY_READY",
            DecisionStatus.BUY_TRIGGERED: "BUY_TRIGGERED",
            DecisionStatus.HOLD: "HOLD_ENTERED",
            DecisionStatus.SELL_WARNING: "SELL_WARNING",
            DecisionStatus.SELL_TRIGGERED: "SELL_TRIGGERED",
            DecisionStatus.INVALIDATED: "INVALIDATED",
        }
        return mapping.get(status, "STATUS_CHANGE")

    @staticmethod
    def _alert_message(status: DecisionStatus,
                       candidate: dict[str, Any] | None) -> str:
        symbol = candidate.get("symbol", "?") if candidate else "?"
        mapping = {
            DecisionStatus.WATCH: f"Pattern detected, monitoring {symbol}",
            DecisionStatus.BUY_READY: f"Conditions met, {symbol} ready for entry",
            DecisionStatus.BUY_TRIGGERED: f"Buy intent created for {symbol}",
            DecisionStatus.HOLD: f"Position entered in {symbol}",
            DecisionStatus.SELL_WARNING: f"Exit signal approaching for {symbol}",
            DecisionStatus.SELL_TRIGGERED: f"Exit executed for {symbol}",
            DecisionStatus.INVALIDATED: f"Decision invalidated for {symbol}",
        }
        return mapping.get(status, f"Status changed to {status.value}")

    # ── Build a SignalDecision ────────────────────────────────────────

    def _build_decision(self, *,
                        status: DecisionStatus,
                        context: dict[str, Any],
                        candidate: dict[str, Any] | None,
                        reason: str = "",
                        event_id: str | None = None,
                        ) -> SignalDecision:
        """Construct a SignalDecision from current state."""
        symbol = candidate["symbol"] if candidate else self.state.symbol
        pattern_id = candidate["pattern_id"] if candidate else self.state.pattern_id
        pattern_version = candidate["pattern_version"] if candidate else self.state.pattern_version

        decision_id = f"{symbol}_{pattern_id}_{uuid.uuid4().hex[:12]}"

        alerts = []
        if reason:
            alerts.append({
                "alert_type": "DECISION_NOTE",
                "message": reason,
                "as_of": context.get("as_of", _now_iso()),
                "evidence_ref": event_id,
            })

        return SignalDecision(
            decision_id=decision_id,
            symbol=symbol,
            pattern_id=pattern_id,
            pattern_version=pattern_version,
            status=status,
            context=context,
            candidate=dict(candidate) if candidate else None,
            entry_plan=None,
            exit_plan=None,
            alerts=alerts,
            created_at=_now_iso(),
            state_version=1,
        )

    # ── Transition reason helper ──────────────────────────────────────

    @staticmethod
    def _transition_reason(target: DecisionStatus) -> str:
        mapping = {
            DecisionStatus.BUY_READY: "entry_conditions_met",
            DecisionStatus.BUY_TRIGGERED: "buy_intent_created",
            DecisionStatus.HOLD: "position_filled",
            DecisionStatus.SELL_WARNING: "exit_signal_nearing",
            DecisionStatus.SELL_TRIGGERED: "exit_executed",
            DecisionStatus.INVALIDATED: "candidate_revoked",
        }
        return mapping.get(target, f"transition_to_{target.value}")


# ── Helpers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
