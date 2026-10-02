from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from jsonschema import validate as validate_json_schema

from .base import LLMClient


class LLMConfigurationError(RuntimeError):
    pass


class LLMTransportError(RuntimeError):
    pass


def _value(cfg: dict[str, Any], key: str, env_key: str, *, required: bool = False) -> str | None:
    direct = cfg.get(key)
    if direct is not None and str(direct).strip():
        return str(direct)
    env_name = cfg.get(env_key)
    if env_name:
        v = os.environ.get(str(env_name))
        if v:
            return v
        if required:
            raise LLMConfigurationError(f"missing required environment variable: {env_name}")
    if required:
        raise LLMConfigurationError(f"missing required LLM setting: {key} or {env_key}")
    return None


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.S | re.I)
    if fence:
        stripped = fence.group(1).strip()
    try:
        out = json.loads(stripped)
    except json.JSONDecodeError as exc:
        # Conservative fallback: only accept a single balanced JSON object.
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise LLMTransportError("LLM response is not valid JSON") from exc
        try:
            out = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as inner:
            raise LLMTransportError("LLM response is not valid JSON") from inner
    if not isinstance(out, dict):
        raise LLMTransportError("LLM JSON response must be an object")
    return out


class OpenAICompatibleLLMClient(LLMClient):
    """Minimal OpenAI-compatible Chat Completions client.

    Supports direct config or environment-variable references. API keys never appear
    in exceptions or returned objects. Structured JSON Schema output is attempted first
    and can fall back to JSON object mode for compatible providers that do not support
    json_schema.
    """

    def __init__(self, config: dict[str, Any], *, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.base_url = (_value(config, "base_url", "base_url_env", required=True) or "").rstrip("/")
        self.api_key = _value(config, "api_key", "api_key_env", required=True) or ""
        self.model = _value(config, "model", "model_env", required=True) or ""
        self.path = str(config.get("chat_completions_path", "/v1/chat/completions"))
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        self.timeout = float(config.get("timeout_seconds", 60.0))
        self.retries = int(config.get("retries", 2))
        self.temperature = float(config.get("temperature", 0.0))
        self.max_tokens = int(config.get("max_tokens", 4096))
        self.structured_output = str(config.get("structured_output", "auto")).lower()
        self.client = httpx.Client(
            timeout=httpx.Timeout(self.timeout),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            transport=transport,
            follow_redirects=True,
            trust_env=False,
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/v1") and self.path.startswith("/v1/"):
            return self.base_url + self.path[3:]
        return self.base_url + self.path

    def close(self) -> None:
        self.client.close()

    def _payload(self, system_prompt: str, user_payload: dict[str, Any], schema: dict[str, Any] | None,
                 *, schema_mode: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if schema and schema_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "a_share_signal", "strict": True, "schema": schema},
            }
        elif schema_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.post(self.endpoint, json=payload)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise LLMTransportError(f"LLM transport failure: {type(exc).__name__}") from exc
            if resp.status_code in (401, 403):
                raise LLMTransportError(f"LLM authentication failed (HTTP {resp.status_code})")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
            if resp.status_code >= 400:
                body = resp.text[:500].replace("\n", " ")
                raise LLMTransportError(f"LLM HTTP {resp.status_code}: {body}")
            return resp
        raise LLMTransportError(f"LLM request failed: {type(last_exc).__name__ if last_exc else 'unknown'}")

    def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
        modes: list[str]
        if self.structured_output == "json_schema":
            modes = ["json_schema"]
        elif self.structured_output == "json_object":
            modes = ["json_object"]
        elif self.structured_output == "none":
            modes = ["none"]
        else:
            modes = ["json_schema", "json_object"] if schema else ["json_object"]

        last_error: Exception | None = None
        for i, mode in enumerate(modes):
            try:
                resp = self._request(self._payload(system_prompt, user_payload, schema, schema_mode=mode))
                obj = resp.json()
                choices = obj.get("choices") if isinstance(obj, dict) else None
                if not choices:
                    raise LLMTransportError("LLM response contains no choices")
                message = choices[0].get("message", {})
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
                if not isinstance(content, str):
                    raise LLMTransportError("LLM response content is missing")
                out = _extract_json(content)
                # Compatibility normalization for providers that honor JSON-only but
                # shape the root object differently than the requested schema.
                if schema is not None and isinstance(out, dict):
                    if "decisions" in schema.get("properties", {}) and "decisions" not in out:
                        if "candidates" in out and isinstance(out["candidates"], list):
                            out["decisions"] = out.pop("candidates")
                        elif out and all(isinstance(v, dict) and "candidate_id" in v for v in out.values()):
                            out = {"decisions": list(out.values())}
                    if "decisions" in out and isinstance(out["decisions"], list):
                        for item in out["decisions"]:
                            if isinstance(item, dict) and "confidence" in item:
                                value = item["confidence"]
                                if isinstance(value, (int, float)) and 0.0 < value <= 1.0:
                                    item["confidence"] = round(value * 100.0, 1)
                    try:
                        validate_json_schema(instance=out, schema=schema)
                    except Exception as exc:
                        raise LLMTransportError(f"LLM JSON schema validation failed: {type(exc).__name__}") from exc
                return out
            except LLMTransportError as exc:
                last_error = exc
                # Fallback only when json_schema compatibility is the likely problem.
                if mode == "json_schema" and i + 1 < len(modes) and ("HTTP 400" in str(exc) or "HTTP 422" in str(exc)):
                    continue
                raise
        raise last_error or LLMTransportError("LLM request failed")

    def probe(self) -> dict[str, Any]:
        try:
            result = self.complete_json(
                system_prompt="Return JSON only.",
                user_payload={"task": "health_check", "expected": {"ok": True}},
                schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": True,
                },
            )
            return {"ok": bool(result.get("ok", True)), "base_url": self.base_url, "model": self.model, "response": result}
        except Exception as exc:
            return {"ok": False, "base_url": self.base_url, "model": self.model, "error": str(exc)}
