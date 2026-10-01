from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from jsonschema import Draft202012Validator

from ..config import RuntimeConfig
from ..utils import canonical_json, now_shanghai, redact, sha256_hex
from .audit_index import AuditIndex
from .snapshot_store import SnapshotStore


class AuditWriteError(RuntimeError):
    pass


class AuditEventWriter:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        log_cfg = config.logging.get("logging", {})
        paths = config.logging.get("paths", {})
        root_rel = Path(paths.get("root", "data/audit"))
        self.audit_root = (config.project_root / root_rel).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        index_rel = paths.get("index_db", "{root}/audit_index.sqlite").replace("{root}", str(root_rel))
        self.index = AuditIndex((config.project_root / index_rel).resolve())
        privacy = config.logging.get("privacy_security", {})
        self.never_log = set(privacy.get("never_log", []))
        self.hash_fields = set(privacy.get("hash_fields", []))
        self.snapshot_store = SnapshotStore(self.audit_root, self.never_log, self.hash_fields)
        self.critical = set(config.logging.get("critical_event_types", []))
        self.max_inline_kb = int(config.logging.get("candidate_logging", {}).get("max_inline_payload_kb", 64))
        schema_path = config.project_root / "skill" / "schemas" / "audit_event.schema.json"
        self.validator = None
        if schema_path.exists():
            self.validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))

    def _daily_file(self, trade_date: str) -> Path:
        y, m, d = trade_date.split("-")
        p = self.audit_root / y / m / d / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _last_hash(path: Path) -> str | None:
        if not path.exists() or path.stat().st_size == 0:
            return None
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell() - 1
            # Skip trailing newlines, then find the start of the last record.
            while pos >= 0:
                fh.seek(pos)
                if fh.read(1) not in (b"\n", b"\r"):
                    break
                pos -= 1
            if pos < 0:
                return None
            end = pos + 1
            while pos >= 0:
                fh.seek(pos)
                if fh.read(1) == b"\n":
                    break
                pos -= 1
            start = pos + 1
            fh.seek(start)
            line = fh.read(end - start)
        return json.loads(line)["event_hash"] if line else None

    def write_event(self, *, event_type: str, phase: str, run_id: str, trace_id: str,
                    producer: dict[str, Any], status: str = "ok", payload: Any = None,
                    trade_date: str | None = None, message_id: str | None = None,
                    parent_event_id: str | None = None, intent_id: str | None = None,
                    symbol: str | None = None, strategy_id: str | None = None,
                    strategy_version: str | None = None, source_refs: list[str] | None = None,
                    error_code: str | None = None, prompt_hash: str | None = None,
                    model_id: str | None = None) -> dict[str, Any]:
        now = now_shanghai()
        trade_date = trade_date or now.date().isoformat()
        safe_payload = redact(payload, self.never_log, self.hash_fields)
        raw_payload = canonical_json(safe_payload).encode("utf-8") if payload is not None else b""
        payload_ref = None
        payload_sha = sha256_hex(raw_payload) if payload is not None else None
        inline_payload = safe_payload
        if len(raw_payload) > self.max_inline_kb * 1024:
            snap = self.snapshot_store.put(trade_date, safe_payload)
            payload_ref = snap["snapshot_ref"]
            inline_payload = {"snapshot_ref": payload_ref, "summary": "payload stored by content hash"}

        event = {
            "schema_version": "5.0.0",
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "event_time": now.isoformat(),
            "trade_date": trade_date,
            "phase": phase,
            "run_id": run_id,
            "trace_id": trace_id,
            "message_id": message_id,
            "parent_event_id": parent_event_id,
            "intent_id": intent_id,
            "symbol": symbol,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version or str(self.config.runtime.get("strategy_version", "skill-v5.0.0")),
            "config_hash": self.config.config_hash,
            "prompt_hash": prompt_hash,
            "model_id": model_id or str(self.config.runtime.get("model_id", "external-llm")),
            "code_version": str(self.config.runtime.get("code_version", "runtime-v0.1.0")),
            "producer": producer,
            "status": status,
            "source_refs": source_refs or [],
            "payload": inline_payload,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha,
            "error_code": error_code,
            "previous_event_hash": None,
            "event_hash": "0" * 64,
        }
        path = self._daily_file(trade_date)
        try:
            with path.open("a+", encoding="utf-8") as fh:
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.flush()
                previous_hash = self._last_hash(path)
                event["previous_event_hash"] = previous_hash
                to_hash = dict(event)
                to_hash.pop("event_hash", None)
                event["event_hash"] = sha256_hex(canonical_json(to_hash))
                if self.validator:
                    errors = sorted(self.validator.iter_errors(event), key=lambda e: list(e.path))
                    if errors:
                        raise AuditWriteError("audit schema invalid: " + "; ".join(e.message for e in errors[:5]))
                fh.seek(0, os.SEEK_END)
                line_no = sum(1 for _ in path.open("r", encoding="utf-8")) + 1 if path.stat().st_size else 1
                fh.write(canonical_json(event) + "\n")
                fh.flush()
                if event_type in self.critical or self.config.logging.get("logging", {}).get("flush_policy") == "per_event":
                    os.fsync(fh.fileno())
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            self.index.add(event, str(path), line_no)
            return event
        except Exception as exc:
            raise AuditWriteError(str(exc)) from exc
