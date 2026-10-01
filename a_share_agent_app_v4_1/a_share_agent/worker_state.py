from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .storage.sqlite_store import connect_sqlite
from .utils import now_shanghai


class WorkerStateStore:
    """Persistent scheduler/worker coordination state shared by worker and web.

    The store provides three guarantees:
    1. one active worker lease per project root;
    2. a phase slot is claimed at most once even after process restart;
    3. the web process can observe worker heartbeat/status without importing the worker loop.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = connect_sqlite(self.db_path)
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS worker_lease (
              lease_name TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              heartbeat_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase_runs (
              trade_date TEXT NOT NULL,
              phase TEXT NOT NULL,
              slot_key TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              run_id TEXT,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              error TEXT,
              result_json TEXT,
              PRIMARY KEY(trade_date, phase, slot_key)
            );
            CREATE INDEX IF NOT EXISTS idx_phase_runs_date ON phase_runs(trade_date, started_at);
            CREATE TABLE IF NOT EXISTS worker_status (
              worker_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              pid INTEGER,
              started_at TEXT NOT NULL,
              heartbeat_at TEXT NOT NULL,
              current_phase TEXT,
              current_slot TEXT,
              last_phase TEXT,
              last_success_at TEXT,
              last_error TEXT,
              metadata_json TEXT
            );
            """
        )

    def acquire_lease(self, owner_id: str, *, ttl_seconds: int = 30, lease_name: str = "scheduler") -> bool:
        now = now_shanghai()
        expires = now + timedelta(seconds=ttl_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT owner_id, expires_at FROM worker_lease WHERE lease_name=?", (lease_name,)
            ).fetchone()
            if row:
                expiry = datetime.fromisoformat(str(row["expires_at"]))
                if expiry > now and str(row["owner_id"]) != owner_id:
                    self.conn.execute("ROLLBACK")
                    return False
            self.conn.execute(
                "INSERT INTO worker_lease(lease_name,owner_id,acquired_at,heartbeat_at,expires_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(lease_name) DO UPDATE SET "
                "owner_id=excluded.owner_id, acquired_at=excluded.acquired_at, heartbeat_at=excluded.heartbeat_at, expires_at=excluded.expires_at",
                (lease_name, owner_id, now.isoformat(), now.isoformat(), expires.isoformat()),
            )
            self.conn.execute("COMMIT")
            return True
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def renew_lease(self, owner_id: str, *, ttl_seconds: int = 30, lease_name: str = "scheduler") -> bool:
        now = now_shanghai()
        expires = now + timedelta(seconds=ttl_seconds)
        cur = self.conn.execute(
            "UPDATE worker_lease SET heartbeat_at=?, expires_at=? WHERE lease_name=? AND owner_id=?",
            (now.isoformat(), expires.isoformat(), lease_name, owner_id),
        )
        return cur.rowcount == 1

    def release_lease(self, owner_id: str, *, lease_name: str = "scheduler") -> None:
        self.conn.execute("DELETE FROM worker_lease WHERE lease_name=? AND owner_id=?", (lease_name, owner_id))

    def claim_phase(self, *, trade_date: str, phase: str, slot_key: str, owner_id: str) -> bool:
        now = now_shanghai().isoformat()
        try:
            self.conn.execute(
                "INSERT INTO phase_runs(trade_date,phase,slot_key,owner_id,status,started_at) VALUES(?,?,?,?,?,?)",
                (trade_date, phase, slot_key, owner_id, "RUNNING", now),
            )
            return True
        except Exception as exc:
            # SQLite raises IntegrityError for an already claimed slot. Avoid importing
            # sqlite3 here only to keep this store transport-agnostic to the connector.
            if "UNIQUE" in str(exc).upper() or "PRIMARY KEY" in str(exc).upper():
                return False
            raise

    def finish_phase(self, *, trade_date: str, phase: str, slot_key: str, status: str,
                     run_id: str | None = None, result: Any = None, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE phase_runs SET status=?,run_id=?,finished_at=?,error=?,result_json=? "
            "WHERE trade_date=? AND phase=? AND slot_key=?",
            (
                status,
                run_id,
                now_shanghai().isoformat(),
                error,
                json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                trade_date,
                phase,
                slot_key,
            ),
        )

    def update_status(self, *, worker_id: str, state: str, pid: int | None = None,
                      current_phase: str | None = None, current_slot: str | None = None,
                      last_phase: str | None = None, last_success_at: str | None = None,
                      last_error: str | None = None, metadata: dict[str, Any] | None = None,
                      started_at: str | None = None) -> None:
        now = now_shanghai().isoformat()
        existing = self.conn.execute("SELECT started_at FROM worker_status WHERE worker_id=?", (worker_id,)).fetchone()
        start = started_at or (str(existing["started_at"]) if existing else now)
        self.conn.execute(
            "INSERT INTO worker_status(worker_id,state,pid,started_at,heartbeat_at,current_phase,current_slot,last_phase,last_success_at,last_error,metadata_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET "
            "state=excluded.state,pid=excluded.pid,heartbeat_at=excluded.heartbeat_at,current_phase=excluded.current_phase,"
            "current_slot=excluded.current_slot,last_phase=excluded.last_phase,last_success_at=excluded.last_success_at,"
            "last_error=excluded.last_error,metadata_json=excluded.metadata_json",
            (
                worker_id, state, pid, start, now, current_phase, current_slot, last_phase,
                last_success_at, last_error,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
            ),
        )

    def latest_status(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM worker_status ORDER BY heartbeat_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
        except Exception:
            out["metadata"] = {}
        return out

    def phase_history(self, trade_date: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM phase_runs WHERE trade_date=? ORDER BY started_at DESC LIMIT ?",
            (trade_date, limit),
        ).fetchall()
        return [dict(r) for r in rows]
