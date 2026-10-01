from typing import Any


class YourLLMClient:
    def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call your LLM here and return a parsed JSON object matching signal_output.schema.json."""
        raise NotImplementedError("Connect this to the LLM provider used by your autonomous trading software")
