from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import IntentRecord
from ..storage.sqlite_store import connect_sqlite
from ..utils import now_shanghai


class DuplicateIntent(RuntimeError):
    pass


class IntentStore:
    def __init__(self, db_path: str | Path):
        self.conn = connect_sqlite(db_path)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS intents (
          intent_id TEXT PRIMARY KEY,
          idempotency_key TEXT NOT NULL UNIQUE,
          trade_date TEXT NOT NULL,
          symbol TEXT NOT NULL,
          direction TEXT NOT NULL,
          strategy_id TEXT NOT NULL,
          phase TEXT NOT NULL,
          max_quantity INTEGER NOT NULL,
          limit_price REAL NOT NULL,
          stop_price REAL,
          expires_at TEXT NOT NULL,
          reason TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_intent_symbol_day ON intents(trade_date,symbol,direction,status);
        """)

    def create(self, rec: IntentRecord) -> IntentRecord:
        now = now_shanghai().isoformat()
        rec.created_at = rec.created_at or now; rec.updated_at = now
        try:
            self.conn.execute("""INSERT INTO intents(intent_id,idempotency_key,trade_date,symbol,direction,strategy_id,phase,max_quantity,limit_price,stop_price,expires_at,reason,status,created_at,updated_at,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                rec.intent_id, rec.idempotency_key, rec.trade_date, rec.symbol, rec.direction, rec.strategy_id, rec.phase,
                rec.max_quantity, rec.limit_price, rec.stop_price, rec.expires_at, rec.reason, rec.status, rec.created_at, rec.updated_at,
                json.dumps(rec.metadata, ensure_ascii=False),
            ))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DuplicateIntent(rec.idempotency_key) from exc
            raise
        return rec

    def set_status(self, intent_id: str, status: str, metadata_patch: dict[str, Any] | None = None) -> None:
        row = self.get(intent_id)
        if not row: raise KeyError(intent_id)
        meta = row.get("metadata", {})
        meta.update(metadata_patch or {})
        self.conn.execute("UPDATE intents SET status=?,updated_at=?,metadata_json=? WHERE intent_id=?", (status, now_shanghai().isoformat(), json.dumps(meta, ensure_ascii=False), intent_id))

    def get(self, intent_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        if not row: return None
        out = dict(row); out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
        return out

    def by_key(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT intent_id FROM intents WHERE idempotency_key=?", (key,)).fetchone()
        return self.get(row["intent_id"]) if row else None

    def has_entry_today(self, trade_date: str, symbol: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM intents WHERE trade_date=? AND symbol=? AND direction='BUY' AND status NOT IN ('REJECTED','EXPIRED','CANCELLED') LIMIT 1", (trade_date, symbol)).fetchone()
        return bool(row)
