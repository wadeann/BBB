from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.sqlite_store import connect_sqlite


class AuditIndex:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = connect_sqlite(self.db_path)
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          event_time TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          event_type TEXT NOT NULL,
          phase TEXT,
          run_id TEXT,
          trace_id TEXT,
          message_id TEXT,
          parent_event_id TEXT,
          intent_id TEXT,
          symbol TEXT,
          strategy_id TEXT,
          status TEXT,
          payload_ref TEXT,
          payload_sha256 TEXT,
          event_hash TEXT NOT NULL,
          previous_event_hash TEXT,
          file_path TEXT NOT NULL,
          line_no INTEGER NOT NULL,
          payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_trade_date ON events(trade_date, event_time);
        CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id, event_time);
        CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(trade_date, symbol, event_time);
        CREATE INDEX IF NOT EXISTS idx_events_intent ON events(intent_id, event_time);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(trade_date, event_type, event_time);
        """)

    def add(self, event: dict[str, Any], file_path: str, line_no: int) -> None:
        payload = event.get("payload")
        self.conn.execute("""
          INSERT OR REPLACE INTO events(
            event_id,event_time,trade_date,event_type,phase,run_id,trace_id,message_id,parent_event_id,
            intent_id,symbol,strategy_id,status,payload_ref,payload_sha256,event_hash,previous_event_hash,
            file_path,line_no,payload_json
          ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            event["event_id"], event["event_time"], event["trade_date"], event["event_type"], event.get("phase"),
            event.get("run_id"), event.get("trace_id"), event.get("message_id"), event.get("parent_event_id"),
            event.get("intent_id"), event.get("symbol"), event.get("strategy_id"), event.get("status"),
            event.get("payload_ref"), event.get("payload_sha256"), event["event_hash"], event.get("previous_event_hash"),
            file_path, line_no, json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        ))

    def query(self, *, trade_date: str | None = None, event_type: str | None = None,
              trace_id: str | None = None, symbol: str | None = None, intent_id: str | None = None,
              limit: int = 10000) -> list[dict[str, Any]]:
        clauses, args = [], []
        for col, val in (("trade_date", trade_date), ("event_type", event_type), ("trace_id", trace_id),
                         ("symbol", symbol), ("intent_id", intent_id)):
            if val is not None:
                clauses.append(f"{col}=?")
                args.append(val)
        sql = "SELECT * FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY event_time,event_id LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def latest(self, trade_date: str, event_type: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM events WHERE trade_date=? AND event_type=? ORDER BY event_time DESC,line_no DESC LIMIT 1",
            (trade_date, event_type),
        ).fetchone()
        return dict(row) if row else None

    def count(self, trade_date: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM events WHERE trade_date=?", (trade_date,)).fetchone()
        return int(row["n"])

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM events WHERE event_id=? LIMIT 1", (event_id,)).fetchone()
        return dict(row) if row else None

    def trade_dates(self, limit: int = 120) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT trade_date FROM events ORDER BY trade_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [str(r["trade_date"]) for r in rows]

    def event_type_counts(self, trade_date: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM events WHERE trade_date=? GROUP BY event_type ORDER BY n DESC,event_type",
            (trade_date,),
        ).fetchall()
        return {str(r["event_type"]): int(r["n"]) for r in rows}


    def events_after_seq(self, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT rowid AS seq,* FROM events WHERE rowid>? ORDER BY rowid LIMIT ?",
            (int(after_seq), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def max_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(rowid),0) AS n FROM events").fetchone()
        return int(row["n"] if row else 0)

    def trace_ids(self, trade_date: str, limit: int = 500) -> list[str]:
        rows = self.conn.execute(
            "SELECT trace_id, MIN(event_time) AS first_time FROM events "
            "WHERE trade_date=? AND trace_id IS NOT NULL GROUP BY trace_id ORDER BY first_time LIMIT ?",
            (trade_date, limit),
        ).fetchall()
        return [str(r["trace_id"]) for r in rows if r["trace_id"]]
