from __future__ import annotations

from dataclasses import dataclass


ORDERED_PHASES = [
    "RECOVERY_SYNC", "PREOPEN_PRECHECK", "PREOPEN_CONTEXT", "OPEN_AUCTION_OBSERVE", "OPEN_AUCTION_FREEZE",
    "OPEN_RECONCILE", "OPEN_STABILIZE", "ENTRY_WINDOW_AM", "MORNING_MONITOR", "MORNING_CLOSE_PROTECT",
    "MIDDAY_REVIEW", "PM_PRECHECK", "PM_STABILIZE", "ENTRY_WINDOW_PM", "LATE_SESSION_MANAGE",
    "CLOSE_AUCTION_FREEZE", "CLOSE_RECONCILE", "EOD_UNIVERSE_SCAN", "EOD_DEEP_DIVE", "NIGHT_EVENT_CHECK",
    "DAILY_REVIEW", "AUDIT_FINALIZE"
]


@dataclass
class RuntimeState:
    phase: str | None = None
    degraded_no_order: bool = False
    recovered: bool = False
    last_run_id: str | None = None


class StateMachine:
    def __init__(self):
        self.state = RuntimeState()

    def transition(self, phase: str, *, after_restart: bool = False) -> RuntimeState:
        if phase not in ORDERED_PHASES:
            raise ValueError(f"invalid phase {phase}")
        if after_restart and phase != "RECOVERY_SYNC" and not self.state.recovered:
            raise RuntimeError("restart requires RECOVERY_SYNC before resuming")
        if phase == "RECOVERY_SYNC":
            self.state.phase = phase
            return self.state
        self.state.phase = phase
        return self.state

    def mark_recovered(self) -> None:
        self.state.recovered = True
        self.state.degraded_no_order = False

    def degrade(self) -> None:
        self.state.degraded_no_order = True
