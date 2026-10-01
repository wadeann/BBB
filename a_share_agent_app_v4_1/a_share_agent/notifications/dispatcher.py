from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..audit.audit_index import AuditIndex
from ..config import RuntimeConfig
from .channels import FeishuWebhookChannel
from .formatter import NotificationFormatter
from .store import NotificationStore


class NotificationDispatcher:
    """Tail AuditIndex and reliably deliver selected events to outbound channels."""

    def __init__(self, config: RuntimeConfig, audit_index: AuditIndex, *, channels: dict[str, Any] | None = None):
        self.config = config
        self.audit_index = audit_index
        cfg = config.runtime.get("notifications", {}) or {}
        self.cfg = cfg
        state_rel = cfg.get("state_db", "data/audit/notification_state.sqlite")
        self.store = NotificationStore(config.project_root / state_rel)
        self.formatter = NotificationFormatter(cfg.get("rules", {}))
        self.poll_seconds = float(cfg.get("poll_seconds", 1.0))
        self.max_attempts = int(cfg.get("max_attempts", 5))
        self.backoff_seconds = [int(x) for x in cfg.get("backoff_seconds", [2, 10, 30, 120, 300])]
        self.enabled = bool(cfg.get("enabled", True))
        self.channels: dict[str, Any] = channels or {}
        if not channels:
            feishu_cfg = cfg.get("feishu", {}) or {}
            if bool(feishu_cfg.get("enabled", False)):
                self.channels["feishu"] = FeishuWebhookChannel(feishu_cfg)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notification-dispatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.drain_once()
            except Exception:
                # Delivery failures are persisted per item; a top-level loop failure
                # must never take down the trading worker.
                pass
            self._stop.wait(self.poll_seconds)

    def _enabled_channels_for(self, event: dict[str, Any]) -> list[str]:
        if not self.formatter.should_notify(event):
            return []
        et = str(event.get("event_type") or "")
        out: list[str] = []
        for name, channel in self.channels.items():
            cfg = self.cfg.get(name, {}) or {}
            event_types = set(cfg.get("event_types", []))
            if event_types and et not in event_types:
                continue
            out.append(name)
        return out

    def ingest(self, limit: int = 500) -> int:
        cursor = self.store.get_cursor()
        rows = self.audit_index.events_after_seq(cursor, limit)
        if not rows:
            return 0
        for row in rows:
            for channel in self._enabled_channels_for(row):
                self.store.enqueue(
                    event_seq=int(row["seq"]), event_id=str(row["event_id"]), event_type=str(row["event_type"]),
                    channel=channel, payload={"event_id": row["event_id"]},
                )
            cursor = max(cursor, int(row["seq"]))
        self.store.set_cursor(cursor)
        return len(rows)

    def deliver(self, limit: int = 100) -> int:
        sent = 0
        for item in self.store.pending(limit):
            channel = self.channels.get(str(item["channel"]))
            if channel is None:
                self._retry(item, RuntimeError("notification channel not available"))
                continue
            event = self.audit_index.get_event(str(item["event_id"]))
            if not event:
                self._retry(item, RuntimeError("source audit event not found"), terminal=True)
                continue
            try:
                msg = self.formatter.format(event)
                channel.send(msg)
                self.store.mark_sent(int(item["id"]))
                sent += 1
            except Exception as exc:
                attempts_after = int(item.get("attempts", 0)) + 1
                self._retry(item, exc, terminal=attempts_after >= self.max_attempts)
        return sent

    def _retry(self, item: dict[str, Any], exc: Exception, terminal: bool = False) -> None:
        attempts_after = int(item.get("attempts", 0)) + 1
        idx = min(max(attempts_after - 1, 0), max(len(self.backoff_seconds) - 1, 0))
        delay = self.backoff_seconds[idx] if self.backoff_seconds else 30
        nxt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        self.store.mark_retry(int(item["id"]), error=str(exc), next_attempt_at=nxt, terminal=terminal)

    def drain_once(self) -> dict[str, int]:
        if not self.enabled:
            return {"ingested": 0, "sent": 0}
        ingested = self.ingest()
        sent = self.deliver()
        return {"ingested": ingested, "sent": sent}

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.recent(limit)

    def probe(self, channel_name: str = "feishu") -> dict[str, Any]:
        channel = self.channels.get(channel_name)
        if channel is None:
            return {"ok": False, "channel": channel_name, "error": "channel disabled or unavailable"}
        if hasattr(channel, "probe"):
            return channel.probe()
        return {"ok": False, "channel": channel_name, "error": "probe unsupported"}
