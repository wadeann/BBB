from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..storage.sqlite_store import connect_sqlite


class NotificationStore:
    """Persistent queue/state for outbound notifications.

    Audit events are immutable truth; this database tracks only delivery state.
    The UNIQUE(event_id, channel) constraint makes restarts and retries idempotent.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = connect_sqlite(self.db_path)
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notification_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_deliveries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_seq INTEGER NOT NULL,
              event_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              channel TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT,
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              sent_at TEXT,
              payload_json TEXT,
              UNIQUE(event_id, channel)
            );
            CREATE INDEX IF NOT EXISTS idx_notification_pending
              ON notification_deliveries(status, next_attempt_at, id);
            CREATE INDEX IF NOT EXISTS idx_notification_event
              ON notification_deliveries(event_id, channel);
            """
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_cursor(self) -> int:
        row = self.conn.execute("SELECT value FROM notification_meta WHERE key='audit_cursor'").fetchone()
        return int(row["value"]) if row else 0

    def set_cursor(self, seq: int) -> None:
        self.conn.execute(
            "INSERT INTO notification_meta(key,value) VALUES('audit_cursor',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(int(seq)),),
        )

    def enqueue(self, *, event_seq: int, event_id: str, event_type: str, channel: str, payload: dict[str, Any]) -> None:
        now = self._now()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO notification_deliveries(
              event_seq,event_id,event_type,channel,status,attempts,next_attempt_at,
              created_at,updated_at,payload_json
            ) VALUES(?,?,?,?, 'PENDING',0,?,?,?,?)
            """,
            (int(event_seq), event_id, event_type, channel, now, now, now, json.dumps(payload, ensure_ascii=False)),
        )

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        now = self._now()
        rows = self.conn.execute(
            """
            SELECT * FROM notification_deliveries
            WHERE status IN ('PENDING','RETRY')
              AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY id LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_sent(self, delivery_id: int) -> None:
        now = self._now()
        self.conn.execute(
            "UPDATE notification_deliveries SET status='SENT',attempts=attempts+1,updated_at=?,sent_at=?,last_error=NULL WHERE id=?",
            (now, now, int(delivery_id)),
        )

    def mark_retry(self, delivery_id: int, *, error: str, next_attempt_at: str, terminal: bool = False) -> None:
        now = self._now()
        status = "FAILED" if terminal else "RETRY"
        self.conn.execute(
            "UPDATE notification_deliveries SET status=?,attempts=attempts+1,updated_at=?,next_attempt_at=?,last_error=? WHERE id=?",
            (status, now, next_attempt_at, str(error)[:1000], int(delivery_id)),
        )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM notification_deliveries ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d["payload"] = json.loads(d.pop("payload_json") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out
