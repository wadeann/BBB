"""Phase 5: Live Decision State Machine.

Defines the finite state machine for per-symbol trading decisions and
the SignalDecision output contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ── Status values ─────────────────────────────────────────────────────

class DecisionStatus(StrEnum):
    """Lifecycle status for a single symbol's decision state."""
    WATCH = "WATCH"                     # pattern detected, monitoring
    BUY_READY = "BUY_READY"             # conditions met, price OK
    BUY_TRIGGERED = "BUY_TRIGGERED"     # intent created
    HOLD = "HOLD"                       # filled, in position
    SELL_WARNING = "SELL_WARNING"       # exit signal conditions approaching
    SELL_TRIGGERED = "SELL_TRIGGERED"   # exit executed
    INVALIDATED = "INVALIDATED"         # revoked, market changed, PANIC


# ── Legal transitions ─────────────────────────────────────────────────

_TRANSITIONS: dict[DecisionStatus, set[DecisionStatus | None]] = {
    DecisionStatus.WATCH: {DecisionStatus.BUY_READY, DecisionStatus.INVALIDATED},
    DecisionStatus.BUY_READY: {DecisionStatus.BUY_TRIGGERED, DecisionStatus.INVALIDATED},
    DecisionStatus.BUY_TRIGGERED: {DecisionStatus.HOLD, DecisionStatus.INVALIDATED},
    DecisionStatus.HOLD: {DecisionStatus.SELL_WARNING, DecisionStatus.INVALIDATED},
    DecisionStatus.SELL_WARNING: {DecisionStatus.SELL_TRIGGERED, DecisionStatus.INVALIDATED},
    DecisionStatus.SELL_TRIGGERED: set(),  # terminal (re-entry creates new chain)
    DecisionStatus.INVALIDATED: set(),     # terminal
}


def is_valid_transition(from_status: DecisionStatus,
                        to_status: DecisionStatus) -> bool:
    """Return True iff *to_status* is reachable from *from_status*."""
    allowed = _TRANSITIONS.get(from_status, set())
    return to_status in allowed


# ── Transition audit record ───────────────────────────────────────────

@dataclass(frozen=True)
class TransitionRecord:
    """Single auditable state transition."""
    from_status: DecisionStatus | None
    to_status: DecisionStatus
    reason: str
    timestamp: str  # ISO-8601
    trigger: str = "manual"  # source of the transition


# ── SignalDecision output contract ────────────────────────────────────

@dataclass
class SignalDecision:
    """Primary output of the Decision Engine, consumed by P6 Paper Trading."""
    decision_id: str
    symbol: str
    pattern_id: str
    pattern_version: str
    status: DecisionStatus
    context: dict[str, Any]          # ContextSnapshot at decision time
    candidate: dict[str, Any] | None  # serialized Candidate (None for exit transitions)
    entry_plan: dict[str, Any] | None
    exit_plan: dict[str, Any] | None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""             # ISO timestamp, set in factory
    state_version: int = 1

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "status": str(self.status),
            "context": dict(self.context),
            "candidate": dict(self.candidate) if self.candidate else None,
            "entry_plan": dict(self.entry_plan) if self.entry_plan else None,
            "exit_plan": dict(self.exit_plan) if self.exit_plan else None,
            "alerts": list(self.alerts),
            "created_at": self.created_at,
            "state_version": self.state_version,
        }


# ── Per-symbol state machine ──────────────────────────────────────────

class DecisionState:
    """Finite state machine governing one symbol's decision lifecycle.

    Each instance tracks:
    - Current status
    - Full transition audit trail
    - The last event_id seen (for ordering checks)
    """

    def __init__(self, symbol: str, pattern_id: str, pattern_version: str) -> None:
        self.symbol = symbol
        self.pattern_id = pattern_id
        self.pattern_version = pattern_version
        self.status: DecisionStatus = DecisionStatus.WATCH
        self.audit_trail: list[TransitionRecord] = []
        self.last_event_id: str | None = None
        self.last_event_seq: int = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        return self.status in (DecisionStatus.SELL_TRIGGERED,
                               DecisionStatus.INVALIDATED)

    @property
    def can_enter(self) -> bool:
        """Whether the state machine can accept a new entry transition."""
        return self.status in (DecisionStatus.WATCH, DecisionStatus.BUY_READY)

    # ── Core transition ───────────────────────────────────────────────

    def transition(self, to_status: DecisionStatus, *,
                   reason: str = "",
                   trigger: str = "engine",
                   event_id: str | None = None,
                   event_seq: int = 0) -> TransitionRecord:
        """Attempt a state transition.

        Returns the TransitionRecord on success.
        Raises ``ValueError`` for illegal transitions.
        """
        if not is_valid_transition(self.status, to_status):
            raise ValueError(
                f"Illegal transition {self.status} -> {to_status} "
                f"for {self.symbol}/{self.pattern_id}: {reason}"
            )

        ts = _now_iso()
        record = TransitionRecord(
            from_status=self.status,
            to_status=to_status,
            reason=reason,
            timestamp=ts,
            trigger=trigger,
        )
        self.audit_trail.append(record)
        self.status = to_status
        if event_id is not None:
            self.last_event_id = event_id
        if event_seq > 0:
            self.last_event_seq = event_seq
        return record

    # ── Convenience transitions ───────────────────────────────────────

    def mark_buy_ready(self, *, reason: str = "entry_conditions_met",
                       event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.BUY_READY, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    def mark_buy_triggered(self, *, reason: str = "buy_intent_created",
                           event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.BUY_TRIGGERED, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    def mark_hold(self, *, reason: str = "position_filled",
                  event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.HOLD, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    def mark_sell_warning(self, *, reason: str = "exit_signal_nearing",
                          event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.SELL_WARNING, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    def mark_sell_triggered(self, *, reason: str = "exit_executed",
                            event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.SELL_TRIGGERED, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    def invalidate(self, *, reason: str = "candidate_revoked",
                   event_id: str | None = None, event_seq: int = 0) -> TransitionRecord:
        return self.transition(DecisionStatus.INVALIDATED, reason=reason,
                               trigger="engine", event_id=event_id, event_seq=event_seq)

    # ── Event ordering check ──────────────────────────────────────────

    def check_event_order(self, event_id: str, event_seq: int) -> None:
        """Raise ValueError if *event_seq*/*event_id* is replayed or out of order."""
        if event_seq > 0 and event_seq <= self.last_event_seq:
            raise ValueError(
                f"Out-of-order or repeat event seq={event_seq} for "
                f"{self.symbol}/{self.pattern_id} (last seq={self.last_event_seq})"
            )
        if event_id is not None and event_id == self.last_event_id:
            raise ValueError(
                f"Duplicate event_id={event_id} for {self.symbol}/{self.pattern_id}"
            )

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "status": str(self.status),
            "audit_trail": [
                {
                    "from": str(r.from_status) if r.from_status else None,
                    "to": str(r.to_status),
                    "reason": r.reason,
                    "timestamp": r.timestamp,
                    "trigger": r.trigger,
                }
                for r in self.audit_trail
            ],
            "last_event_id": self.last_event_id,
            "last_event_seq": self.last_event_seq,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionState:
        state = cls(
            symbol=data["symbol"],
            pattern_id=data["pattern_id"],
            pattern_version=data["pattern_version"],
        )
        state.status = DecisionStatus(data["status"])
        state.last_event_id = data.get("last_event_id")
        state.last_event_seq = data.get("last_event_seq", 0)
        state.audit_trail = [
            TransitionRecord(
                from_status=DecisionStatus(t["from"]) if t.get("from") else None,
                to_status=DecisionStatus(t["to"]),
                reason=t["reason"],
                timestamp=t["timestamp"],
                trigger=t.get("trigger", "manual"),
            )
            for t in data.get("audit_trail", [])
        ]
        return state


# ── Helpers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
