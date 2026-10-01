from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from ..models import RunContext
from .permissions import PhasePermissions


class Scheduler:
    def __init__(self, schedule_cfg: dict[str, Any], permissions: PhasePermissions, mode: str = "paper"):
        self.cfg = schedule_cfg
        self.permissions = permissions
        self.mode = mode
        self.tz = ZoneInfo(schedule_cfg.get("timezone", "Asia/Shanghai"))
        self.windows = schedule_cfg.get("strategy_safety_windows", {})

    @staticmethod
    def _t(s: str) -> time:
        h, m, sec = map(int, s.split(":"))
        return time(h, m, sec)

    def phase_at(self, dt: datetime) -> str | None:
        local = dt.astimezone(self.tz)
        t = local.time().replace(tzinfo=None)
        for key, (start, end) in self.windows.items():
            if self._t(start) <= t < self._t(end):
                return key.upper()
        return None

    def make_run_context(self, phase: str, dt: datetime | None = None, previous_run_id: str | None = None) -> RunContext:
        dt = (dt or datetime.now(tz=self.tz)).astimezone(self.tz)
        return RunContext(
            run_id=f"{dt:%Y%m%d}-{phase}-{uuid.uuid4().hex[:8]}",
            mode=self.mode,
            phase=phase,
            exchange_timezone=str(self.tz),
            as_of=dt.isoformat(),
            allowed_actions=self.permissions.allowed_actions(phase),
            forbidden_actions=self.permissions.forbidden_actions(phase),
            previous_run_id=previous_run_id,
        )
