from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from .audit.audit_index import AuditIndex


class AuditSSEStream:
    """Cross-process realtime stream backed by AuditIndex.

    Worker writes events into SQLite; the web process tails SQLite and publishes SSE.
    This avoids an in-memory event bus that would break when worker and web are separate
    processes. Clients can reconnect with Last-Event-ID without losing audited events.
    """

    def __init__(self, index: AuditIndex, *, poll_seconds: float = 0.75, heartbeat_seconds: float = 15.0):
        self.index = index
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds

    async def iterate(self, *, after_seq: int = 0) -> AsyncIterator[str]:
        seq = max(0, int(after_seq))
        since_heartbeat = 0.0
        while True:
            rows = await asyncio.to_thread(self.index.events_after_seq, seq, 200)
            if rows:
                for row in rows:
                    seq = int(row["seq"])
                    payload = {
                        "seq": seq,
                        "event_id": row.get("event_id"),
                        "event_time": row.get("event_time"),
                        "trade_date": row.get("trade_date"),
                        "event_type": row.get("event_type"),
                        "phase": row.get("phase"),
                        "symbol": row.get("symbol"),
                        "strategy_id": row.get("strategy_id"),
                        "status": row.get("status"),
                        "trace_id": row.get("trace_id"),
                        "intent_id": row.get("intent_id"),
                    }
                    yield f"id: {seq}\nevent: audit\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                since_heartbeat = 0.0
            else:
                await asyncio.sleep(self.poll_seconds)
                since_heartbeat += self.poll_seconds
                if since_heartbeat >= self.heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    since_heartbeat = 0.0
