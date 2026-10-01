from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]: ...


class NoopLLMClient:
    """Safe default. Runtime can operate its audit/risk/execution plumbing without inventing LLM decisions."""
    def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"decision": "WATCH", "reason": "No LLM client configured", "input_echo": user_payload}
