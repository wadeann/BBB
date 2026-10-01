from __future__ import annotations

import os
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .runtime import AgentRuntime
from .utils import as_trade_date, now_shanghai
from .worker_state import WorkerStateStore


@dataclass(frozen=True)
class PhasePolicy:
    mode: str = "once"  # once | interval
    interval_seconds: int = 0


class BackgroundWorker:
    """Persistent runtime scheduler.

    It intentionally has no web dependency. The worker and web processes coordinate
    only through persistent Audit/SQLite state. A SQLite lease prevents two scheduler
    workers from autonomously executing the same deployment at the same time.
    """

    DEFAULT_INTERVAL_PHASES = {
        "OPEN_AUCTION_OBSERVE": 60,
        "ENTRY_WINDOW_AM": 600,
        "MORNING_MONITOR": 600,
        "ENTRY_WINDOW_PM": 600,
        "LATE_SESSION_MANAGE": 600,
    }

    def __init__(self, runtime: AgentRuntime, *, poll_seconds: float | None = None,
                 owner_id: str | None = None, now_fn: Callable[[], datetime] = now_shanghai):
        self.runtime = runtime
        self.now_fn = now_fn
        cfg = runtime.config.runtime.get("worker", {})
        self.poll_seconds = float(poll_seconds if poll_seconds is not None else cfg.get("poll_seconds", 2.0))
        self.lease_ttl_seconds = int(cfg.get("lease_ttl_seconds", 30))
        self.owner_id = owner_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        db_rel = cfg.get("state_db", "data/audit/worker_state.sqlite")
        self.state = WorkerStateStore(runtime.config.project_root / db_rel)
        self._stop = False
        self._last_phase: str | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None
        self._startup_at = now_fn().isoformat()
        self._policies = self._build_policies(cfg)
        self._calendar_cache: dict[str, bool] = {}

    def _build_policies(self, cfg: dict[str, Any]) -> dict[str, PhasePolicy]:
        out: dict[str, PhasePolicy] = {}
        for phase, seconds in self.DEFAULT_INTERVAL_PHASES.items():
            out[phase] = PhasePolicy("interval", int(seconds))
        custom = cfg.get("phase_policies", {})
        if isinstance(custom, dict):
            for phase, raw in custom.items():
                p = str(phase).upper()
                if isinstance(raw, str):
                    out[p] = PhasePolicy(raw.lower(), 0)
                elif isinstance(raw, dict):
                    out[p] = PhasePolicy(str(raw.get("mode", "once")).lower(), int(raw.get("interval_seconds", 0)))
        return out

    def stop(self, *_: Any) -> None:
        self._stop = True

    def _policy(self, phase: str) -> PhasePolicy:
        return self._policies.get(phase, PhasePolicy("once", 0))

    def _is_trading_day(self, trade_date: str) -> bool:
        """Fail closed when the production calendar cannot be verified.

        The result is cached for the local trade date so a 2-second worker poll does
        not hammer the calendar MCP. FakeMCP returns a deterministic trading day.
        """
        if trade_date in self._calendar_cache:
            return self._calendar_cache[trade_date]
        result = self.runtime.mcp.invoke("mcp_intel_is_trading_day")
        if isinstance(result, bool):
            value = result
        elif isinstance(result, dict):
            raw = result.get("trading_day", result.get("is_trading_day", result.get("isTradingDay")))
            if raw is None:
                raise RuntimeError("trading calendar response missing trading_day")
            value = bool(raw)
        else:
            raise RuntimeError("unsupported trading calendar response")
        self._calendar_cache = {trade_date: value}
        return value

    def slot_key(self, phase: str, dt: datetime) -> str:
        policy = self._policy(phase)
        if policy.mode != "interval" or policy.interval_seconds <= 0:
            return "window"
        seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
        bucket = seconds // policy.interval_seconds
        return f"i{policy.interval_seconds}:{bucket}"

    def _heartbeat(self, state: str, *, phase: str | None = None, slot: str | None = None) -> None:
        self.state.update_status(
            worker_id=self.owner_id,
            state=state,
            pid=os.getpid(),
            current_phase=phase,
            current_slot=slot,
            last_phase=self._last_phase,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            started_at=self._startup_at,
            metadata={"mode": self.runtime.config.mode, "code_version": self.runtime.config.runtime.get("code_version")},
        )

    def startup_recovery(self) -> dict[str, Any]:
        result = self.runtime.run_phase("RECOVERY_SYNC")
        state = str(result.get("state", "UNKNOWN")) if isinstance(result, dict) else "UNKNOWN"
        self._last_phase = "RECOVERY_SYNC"
        if state == "SYNCED":
            self._last_success_at = self.now_fn().isoformat()
            self._last_error = None
        else:
            self._last_error = f"recovery state={state}"
        return result

    def tick(self, dt: datetime | None = None) -> dict[str, Any]:
        now = (dt or self.now_fn()).astimezone(self.runtime.scheduler.tz)
        phase = self.runtime.scheduler.phase_at(now)
        if not phase:
            self._heartbeat("IDLE")
            return {"state": "IDLE", "phase": None}
        slot = self.slot_key(phase, now)
        trade_date = now.date().isoformat()
        try:
            if not self._is_trading_day(trade_date):
                self._heartbeat("NON_TRADING_DAY", phase=phase, slot=slot)
                return {"state": "NON_TRADING_DAY", "phase": phase, "slot": slot}
        except Exception as exc:
            self._last_error = f"calendar check failed: {exc}"
            self._heartbeat("DEGRADED", phase=phase, slot=slot)
            return {"state": "CALENDAR_ERROR", "phase": phase, "slot": slot, "error": str(exc)}
        if not self.state.claim_phase(trade_date=trade_date, phase=phase, slot_key=slot, owner_id=self.owner_id):
            self._heartbeat("WAITING", phase=phase, slot=slot)
            return {"state": "ALREADY_RAN", "phase": phase, "slot": slot}

        self._heartbeat("RUNNING", phase=phase, slot=slot)
        try:
            result = self.runtime.run_phase(phase)
            run_id = None
            # Most phase handlers return business data; run_id is present in the audit log,
            # not necessarily the return value. Persist completion independently.
            self.state.finish_phase(
                trade_date=trade_date, phase=phase, slot_key=slot, status="SUCCESS", run_id=run_id, result=result
            )
            self._last_phase = phase
            self._last_success_at = self.now_fn().isoformat()
            self._last_error = None
            self._heartbeat("RUNNING")
            return {"state": "SUCCESS", "phase": phase, "slot": slot, "result": result}
        except Exception as exc:
            self.state.finish_phase(
                trade_date=trade_date, phase=phase, slot_key=slot, status="ERROR", error=str(exc)
            )
            self._last_phase = phase
            self._last_error = str(exc)
            self._heartbeat("DEGRADED", phase=phase, slot=slot)
            return {"state": "ERROR", "phase": phase, "slot": slot, "error": str(exc)}

    def run_forever(self, *, max_runtime_seconds: float | None = None, run_recovery: bool = True) -> None:
        if not self.state.acquire_lease(self.owner_id, ttl_seconds=self.lease_ttl_seconds):
            raise RuntimeError("another scheduler worker holds the deployment lease")
        self.runtime.notifications.start()
        old_term = old_int = None
        try:
            try:
                old_term = signal.signal(signal.SIGTERM, self.stop)
                old_int = signal.signal(signal.SIGINT, self.stop)
            except ValueError:  # not the main thread, mainly for tests
                pass
            self._heartbeat("STARTING")
            if run_recovery:
                self.startup_recovery()
            started = time.monotonic()
            self._heartbeat("RUNNING")
            while not self._stop:
                if not self.state.renew_lease(self.owner_id, ttl_seconds=self.lease_ttl_seconds):
                    raise RuntimeError("worker lost scheduler lease")
                self.tick()
                if max_runtime_seconds is not None and time.monotonic() - started >= max_runtime_seconds:
                    break
                time.sleep(self.poll_seconds)
        finally:
            self.runtime.notifications.stop()
            self._heartbeat("STOPPED")
            self.state.release_lease(self.owner_id)
            if old_term is not None:
                try: signal.signal(signal.SIGTERM, old_term)
                except ValueError: pass
            if old_int is not None:
                try: signal.signal(signal.SIGINT, old_int)
                except ValueError: pass
