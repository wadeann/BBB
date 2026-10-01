from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ..utils import now_shanghai, parse_iso, stable_hash
from .permissions import PhasePermissions
from .schema_validator import SchemaRegistry


class HandoffRejected(RuntimeError):
    pass


class HandoffContract:
    """Builds and validates v5 upstream/downstream envelopes.

    The contract is deterministic and must run outside the LLM. It checks schema,
    phase, freshness, parent linkage, data quality, and risk gating for order messages.
    """
    def __init__(self, schemas: SchemaRegistry, permissions: PhasePermissions):
        self.schemas = schemas
        self.permissions = permissions
        self.consumed_idempotency_keys: set[str] = set()

    def build(self, *, message_type: str, run_id: str, trace_id: str, phase: str,
              producer: dict[str, Any], payload: dict[str, Any], next_action: str,
              parent_message_id: str | None = None, source_refs: list[str] | None = None,
              data_quality: dict[str, Any] | None = None, freshness_seconds: int = 60,
              idempotency_key: str | None = None, as_of: datetime | None = None) -> dict[str, Any]:
        now = (as_of or now_shanghai())
        env = {
            "schema_version": "5.0.0",
            "message_type": message_type,
            "message_id": str(uuid.uuid4()),
            "parent_message_id": parent_message_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "idempotency_key": idempotency_key or stable_hash([run_id, trace_id, message_type, parent_message_id, payload]),
            "phase": phase,
            "as_of": now.isoformat(),
            "data_cutoff": now.isoformat(),
            "freshness_seconds": int(freshness_seconds),
            "producer": producer,
            "payload": payload,
            "data_quality": data_quality or {"state":"ok","missing":[],"conflicts":[]},
            "source_refs": source_refs or [],
            "errors": [],
            "next_action": next_action,
        }
        self.schemas.validate("handoff_envelope.schema.json", env)
        return env

    def validate_for_consumer(self, env: dict[str, Any], *, expected_phase: str,
                              require_parent: bool = False, require_fresh: bool = True,
                              consume_idempotency: bool = False) -> None:
        self.schemas.validate("handoff_envelope.schema.json", env)
        if env["phase"] != expected_phase:
            raise HandoffRejected(f"phase mismatch: {env['phase']} != {expected_phase}")
        if require_parent and not env.get("parent_message_id"):
            raise HandoffRejected("missing parent_message_id")
        if env.get("data_quality", {}).get("state") == "invalid":
            raise HandoffRejected("invalid data quality")
        if require_fresh:
            cutoff = parse_iso(env["data_cutoff"])
            age = (now_shanghai() - cutoff.astimezone(now_shanghai().tzinfo)).total_seconds()
            if age > int(env.get("freshness_seconds", 0)):
                raise HandoffRejected(f"stale message: age={age:.1f}s")
        if env["message_type"] == "ORDER_REQUEST":
            self.permissions.require(expected_phase, "place_order", direction=str(env["payload"].get("direction", "")))
            if str(env["payload"].get("risk_status", "PASS")).upper() != "PASS":
                raise HandoffRejected("ORDER_REQUEST requires risk_status=PASS")
        key = env["idempotency_key"]
        if consume_idempotency:
            if key in self.consumed_idempotency_keys:
                raise HandoffRejected("idempotency key already consumed")
            self.consumed_idempotency_keys.add(key)
