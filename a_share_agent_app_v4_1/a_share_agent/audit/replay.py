from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..utils import canonical_json, sha256_hex
from .audit_index import AuditIndex
from .snapshot_store import SnapshotStore


class ReplayEngine:
    def __init__(self, audit_root: Path, index: AuditIndex, snapshot_store: SnapshotStore):
        self.audit_root = audit_root
        self.index = index
        self.snapshot_store = snapshot_store

    def _file(self, trade_date: str) -> Path:
        y, m, d = trade_date.split("-")
        return self.audit_root / y / m / d / "events.jsonl"

    def load_day(self, trade_date: str) -> list[dict[str, Any]]:
        path = self._file(trade_date)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def verify_hash_chain(self, trade_date: str) -> dict[str, Any]:
        previous = None
        events = self.load_day(trade_date)
        for i, event in enumerate(events, 1):
            if event.get("previous_event_hash") != previous:
                return {"ok": False, "line": i, "reason": "previous_hash_mismatch", "event_id": event.get("event_id")}
            expected_obj = dict(event)
            actual = expected_obj.pop("event_hash")
            expected = sha256_hex(canonical_json(expected_obj))
            if actual != expected:
                return {"ok": False, "line": i, "reason": "event_hash_mismatch", "event_id": event.get("event_id")}
            previous = actual
        return {"ok": True, "event_count": len(events), "last_hash": previous}

    def exact(self, trade_date: str, *, trace_id: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
        events = self.load_day(trade_date)
        if trace_id:
            events = [e for e in events if e.get("trace_id") == trace_id]
        if symbol:
            events = [e for e in events if e.get("symbol") == symbol]
        return events

    def materialize_payload(self, event: dict[str, Any]) -> Any:
        ref = event.get("payload_ref")
        if ref:
            return self.snapshot_store.get(event["trade_date"], ref)
        return event.get("payload")

    def counterfactual(self, trade_date: str, evaluator: Callable[[list[dict[str, Any]]], Any]) -> Any:
        """Read-only replay. `evaluator` is deliberately not given an execution client."""
        events = self.load_day(trade_date)
        return evaluator(events)
