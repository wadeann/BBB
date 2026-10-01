from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE_PROPOSAL = "live_proposal"


class Phase(StrEnum):
    RECOVERY_SYNC = "RECOVERY_SYNC"
    PREOPEN_PRECHECK = "PREOPEN_PRECHECK"
    PREOPEN_CONTEXT = "PREOPEN_CONTEXT"
    OPEN_AUCTION_OBSERVE = "OPEN_AUCTION_OBSERVE"
    OPEN_AUCTION_FREEZE = "OPEN_AUCTION_FREEZE"
    OPEN_RECONCILE = "OPEN_RECONCILE"
    OPEN_STABILIZE = "OPEN_STABILIZE"
    ENTRY_WINDOW_AM = "ENTRY_WINDOW_AM"
    MORNING_MONITOR = "MORNING_MONITOR"
    MORNING_CLOSE_PROTECT = "MORNING_CLOSE_PROTECT"
    MIDDAY_REVIEW = "MIDDAY_REVIEW"
    PM_PRECHECK = "PM_PRECHECK"
    PM_STABILIZE = "PM_STABILIZE"
    ENTRY_WINDOW_PM = "ENTRY_WINDOW_PM"
    LATE_SESSION_MANAGE = "LATE_SESSION_MANAGE"
    CLOSE_AUCTION_FREEZE = "CLOSE_AUCTION_FREEZE"
    CLOSE_RECONCILE = "CLOSE_RECONCILE"
    EOD_UNIVERSE_SCAN = "EOD_UNIVERSE_SCAN"
    EOD_DEEP_DIVE = "EOD_DEEP_DIVE"
    NIGHT_EVENT_CHECK = "NIGHT_EVENT_CHECK"
    DAILY_REVIEW = "DAILY_REVIEW"
    AUDIT_FINALIZE = "AUDIT_FINALIZE"


@dataclass
class RunContext:
    run_id: str
    mode: str
    phase: str
    exchange_timezone: str
    as_of: str
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    state_version: str | int = 1
    previous_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalRiskResult:
    status: str
    reason_codes: list[str]
    requested_quantity: int
    approved_quantity: int
    risk_amount: float
    risk_per_share: float
    max_position_value: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntentRecord:
    intent_id: str
    idempotency_key: str
    trade_date: str
    symbol: str
    direction: str
    strategy_id: str
    phase: str
    max_quantity: int
    limit_price: float
    stop_price: float | None
    expires_at: str
    reason: str
    status: str = "CREATED"
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
