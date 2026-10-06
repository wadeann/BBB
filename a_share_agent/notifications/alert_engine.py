"""Phase 5: Contextual Alert Engine.

Generates structured alerts from SignalDecision transitions and
persists them through the existing notification infrastructure.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..notifications.store import NotificationStore
from ..utils import now_shanghai

# ── Supported alert types ─────────────────────────────────────────────

ALERT_TYPES = frozenset({
    "WATCH_ADDED",
    "BUY_READY",
    "BUY_TRIGGERED",
    "HOLD_ENTERED",
    "SELL_WARNING",
    "SELL_TRIGGERED",
    "INVALIDATED",
    "STALE_DATA",
    "POLICY_BLOCKED",
    "BAR_ORDER_WARNING",
    "DECISION_NOTE",
})


# ── Alert Engine ──────────────────────────────────────────────────────


class AlertEngine:
    """Generate and enqueue structured alerts for decision events.

    Uses the existing ``NotificationStore`` SQLite queue for persistence,
    which feeds into Feishu/webhook channels via ``NotificationDispatcher``.

    Idempotent — the same ``decision_id`` will NOT re-emit alerts.
    """

    def __init__(self, store: NotificationStore) -> None:
        self.store = store
        self._emitted: set[str] = set()

    # ── Main entry point ──────────────────────────────────────────────

    def emit_alerts(self, *,
                    decision_id: str,
                    status: str,
                    context: dict[str, Any],
                    candidate: dict[str, Any] | None,
                    reason: str = "",
                    pattern_id: str = "",
                    pattern_version: str = "",
                    ) -> list[dict[str, Any]]:
        """Generate and enqueue all alerts for a decision transition.

        Returns the list of emitted alert dicts.
        """
        # Idempotency: skip if already emitted
        if decision_id in self._emitted:
            return []

        alerts: list[dict[str, Any]] = []

        # 1. Primary alert for the status transition
        alert_type = self._alert_type_for(status)
        primary = self._build_alert(
            alert_type=alert_type,
            decision_id=decision_id,
            status=status,
            reason=reason,
            pattern_id=pattern_id,
            pattern_version=pattern_version,
            context=context,
            candidate=candidate,
        )
        alerts.append(primary)

        # 2. If stale data, add a distinct stale-data alert
        if status == "STALE_DATA" or (
            candidate and self._is_stale_data(context)
        ):
            stale = self._build_alert(
                alert_type="STALE_DATA",
                decision_id=decision_id,
                status=status,
                reason="Context data marked as stale — entry blocked",
                pattern_id=pattern_id,
                pattern_version=pattern_version,
                context=context,
                candidate=candidate,
            )
            alerts.append(stale)

        # 3. Persistent queue: enqueue each alert
        for alert in alerts:
            self._enqueue(decision_id, alert)

        self._emitted.add(decision_id)
        return alerts

    # ── Alert builder ─────────────────────────────────────────────────

    def _build_alert(self, *,
                     alert_type: str,
                     decision_id: str,
                     status: str,
                     reason: str,
                     pattern_id: str,
                     pattern_version: str,
                     context: dict[str, Any],
                     candidate: dict[str, Any] | None,
                     ) -> dict[str, Any]:
        """Construct a structured alert dict."""
        now = _now_iso()
        return {
            "alert_type": alert_type,
            "timestamp": now,
            "as_of": context.get("as_of", now),
            "decision_id": decision_id,
            "status": status,
            "pattern": {
                "pattern_id": pattern_id or (candidate.get("pattern_id", "") if candidate else ""),
                "pattern_version": pattern_version or (candidate.get("pattern_version", "") if candidate else "1.0.0"),
            },
            "context": {
                "regime": context.get("regime", ""),
                "theme_lifecycle": context.get("theme_lifecycle", ""),
                "feature_version": context.get("feature_version", ""),
                "trade_date": context.get("trade_date", ""),
                "phase": context.get("phase", ""),
            },
            "entry_info": {
                "entry_price": candidate.get("entry_price") if candidate else None,
                "stop_price": candidate.get("stop_price") if candidate else None,
                "score": candidate.get("score") if candidate else None,
                "score_breakdown": candidate.get("score_breakdown") if candidate else None,
            } if candidate else {},
            "evidence": candidate.get("evidence_summary", {}) if candidate else {},
            "reason": reason,
        }

    # ── Persistent queue ──────────────────────────────────────────────

    def _enqueue(self, decision_id: str, alert: dict[str, Any]) -> None:
        """Write a single alert to the NotificationStore queue."""
        event_seq = self.store.get_cursor() + 1
        payload = {
            "alert_type": alert["alert_type"],
            "decision_id": decision_id,
            "pattern": alert["pattern"],
            "context": alert["context"],
            "entry_info": alert["entry_info"],
            "evidence": alert["evidence"],
            "reason": alert["reason"],
        }
        self.store.enqueue(
            event_seq=event_seq,
            event_id=f"alert_{decision_id}_{alert['alert_type']}",
            event_type=f"SIGNAL_DECISION_{alert['alert_type']}",
            channel="feishu",
            payload=payload,
        )
        self.store.set_cursor(event_seq)

    # ── Idempotency reset ─────────────────────────────────────────────

    def reset_emitted(self) -> None:
        """Clear the idempotency set (for testing or new sessions)."""
        self._emitted.clear()

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _alert_type_for(status: str) -> str:
        """Map a DecisionStatus value to the corresponding alert type."""
        mapping = {
            "WATCH": "WATCH_ADDED",
            "BUY_READY": "BUY_READY",
            "BUY_TRIGGERED": "BUY_TRIGGERED",
            "HOLD": "HOLD_ENTERED",
            "SELL_WARNING": "SELL_WARNING",
            "SELL_TRIGGERED": "SELL_TRIGGERED",
            "INVALIDATED": "INVALIDATED",
        }
        return mapping.get(status, "DECISION_NOTE")

    @staticmethod
    def _is_stale_data(context: dict[str, Any]) -> bool:
        """Check if context indicates stale data."""
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
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > 1800  # 30 min


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
