from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .base import MCPError, MCPInvoker
from .tool_catalog import EXEC_TOOLS, INTEL_TOOLS, JIN10_TOOLS, RISK_TOOLS, OPTIONAL_INTEL_TOOLS


TOOL_TO_SERVICE = {
    **{name: "intel" for name in INTEL_TOOLS},
    **{name: "exec" for name in EXEC_TOOLS},
    **{name: "risk" for name in RISK_TOOLS},
    **{name: "jin10" for name in JIN10_TOOLS},
    **{name: "intel" for name in OPTIONAL_INTEL_TOOLS},
}

EXPECTED_TOOLS_BY_SERVICE = {
    "intel": set(INTEL_TOOLS),
    "risk": set(RISK_TOOLS),
    "exec": set(EXEC_TOOLS),
    "jin10": set(JIN10_TOOLS),
}


class MCPConfigurationError(MCPError):
    pass


@dataclass
class ServiceState:
    initialized: bool = False
    session_id: str | None = None
    negotiated_protocol: str | None = None
    remote_tools: set[str] | None = None


def _env_or_value(cfg: dict[str, Any], value_key: str, env_key: str, *, required: bool = False) -> str | None:
    env_name = cfg.get(env_key)
    if env_name:
        value = os.environ.get(str(env_name))
        if value:
            return value
    value = cfg.get(value_key)
    if value is not None and str(value).strip():
        return str(value)
    if required:
        if env_name:
            raise MCPConfigurationError(f"missing required environment variable: {env_name}")
        raise MCPConfigurationError(f"missing required setting: {value_key} or {env_key}")
    return None


def _build_auth(cfg: dict[str, Any]) -> tuple[httpx.Auth | None, dict[str, str]]:
    auth_cfg = cfg.get("auth") or {}
    auth_type = str(auth_cfg.get("type", "none")).lower()
    headers: dict[str, str] = {}
    if auth_type in {"none", ""}:
        return None, headers
    if auth_type == "basic":
        username = _env_or_value(auth_cfg, "username", "username_env", required=True)
        password = _env_or_value(auth_cfg, "password", "password_env", required=True)
        return httpx.BasicAuth(username or "", password or ""), headers
    if auth_type == "bearer":
        token = _env_or_value(auth_cfg, "token", "token_env", required=True)
        headers["Authorization"] = f"Bearer {token}"
        return None, headers
    if auth_type == "header":
        header_name = str(auth_cfg.get("header_name", "Authorization"))
        value = _env_or_value(auth_cfg, "value", "value_env", required=True)
        headers[header_name] = value or ""
        return None, headers
    raise MCPConfigurationError(f"unsupported MCP auth type: {auth_type}")


def _decode_sse(text: str) -> list[Any]:
    payloads: list[Any] = []
    data_lines: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.strip() and data_lines:
            raw = "\n".join(data_lines)
            data_lines = []
            try:
                payloads.append(json.loads(raw))
            except json.JSONDecodeError:
                payloads.append(raw)
    return payloads


def _unwrap_tool_result(response_obj: Any) -> Any:
    if not isinstance(response_obj, dict):
        return response_obj
    if "error" in response_obj and response_obj.get("error") is not None:
        err = response_obj["error"]
        if isinstance(err, dict):
            raise MCPError(f"MCP JSON-RPC error {err.get('code')}: {err.get('message', 'unknown error')}")
        raise MCPError(f"MCP JSON-RPC error: {err}")
    result = response_obj.get("result", response_obj)
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content")
        raise MCPError(f"MCP tool returned error: {_content_text(content) or 'unknown tool error'}")
    if isinstance(result, dict) and "structuredContent" in result:
        return result["structuredContent"]
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        text = _content_text(content)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return result


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                texts.append(item["text"])
        if texts:
            return "\n".join(texts)
    return None


class StreamableHTTPMCPInvoker(MCPInvoker):
    """MCP Streamable HTTP / JSON-RPC invoker with per-service sessions.

    Credentials are resolved at runtime from env/config and are never included in
    raised errors, audit payloads, or probe output. The caller sees only service URL,
    auth type, HTTP status, and protocol-level errors.
    """

    def __init__(self, config: dict[str, Any], *, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.services = config.get("services") or {}
        self.protocol_version = str(config.get("protocol_version", "2025-06-18"))
        self.client_name = str(config.get("client_name", "a-share-agent"))
        self.client_version = str(config.get("client_version", "0.6.0"))
        self.timeout = float(config.get("timeout_seconds", 20.0))
        self.verify_tls = bool(config.get("verify_tls", True))
        self._transport = transport
        self._clients: dict[str, httpx.Client] = {}
        self._states: dict[str, ServiceState] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._request_id = 0
        self._id_lock = threading.Lock()

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def _next_id(self) -> int:
        with self._id_lock:
            self._request_id += 1
            return self._request_id

    def _service_cfg(self, service: str) -> dict[str, Any]:
        cfg = self.services.get(service)
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            raise MCPConfigurationError(f"MCP service disabled or missing: {service}")
        return cfg

    def _url(self, cfg: dict[str, Any]) -> str:
        url = _env_or_value(cfg, "url", "url_env", required=True)
        return (url or "").rstrip("/")

    def _client(self, service: str) -> httpx.Client:
        if service in self._clients:
            return self._clients[service]
        cfg = self._service_cfg(service)
        auth, auth_headers = _build_auth(cfg)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": f"{self.client_name}/{self.client_version}",
            **auth_headers,
        }
        for k, v in (cfg.get("headers") or {}).items():
            if isinstance(v, str) and v.startswith("env:"):
                env_name = v[4:]
                env_value = os.environ.get(env_name)
                if not env_value:
                    raise MCPConfigurationError(f"missing required environment variable: {env_name}")
                headers[str(k)] = env_value
            else:
                headers[str(k)] = str(v)
        client = httpx.Client(
            timeout=httpx.Timeout(self.timeout), verify=self.verify_tls, auth=auth,
            headers=headers, transport=self._transport, follow_redirects=True,
            trust_env=False,
        )
        self._clients[service] = client
        self._states[service] = ServiceState()
        self._locks[service] = threading.RLock()
        return client

    def _post(self, service: str, payload: dict[str, Any], *, allow_empty: bool = False) -> tuple[Any, httpx.Response]:
        cfg = self._service_cfg(service)
        url = self._url(cfg)
        client = self._client(service)
        state = self._states[service]
        headers: dict[str, str] = {}
        if state.session_id:
            headers["Mcp-Session-Id"] = state.session_id
        if state.negotiated_protocol:
            headers["MCP-Protocol-Version"] = state.negotiated_protocol
        try:
            resp = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP {service} transport failure: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise MCPError(f"MCP {service} authentication failed (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            body = resp.text[:500].replace("\n", " ")
            raise MCPError(f"MCP {service} HTTP {resp.status_code}: {body}")
        sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
        if sid:
            state.session_id = sid
        if not resp.content:
            if allow_empty:
                return None, resp
            return {}, resp
        content_type = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in content_type:
            items = _decode_sse(resp.text)
            if not items:
                return (None if allow_empty else {}), resp
            # Prefer the last JSON-RPC response carrying an id/result/error.
            for item in reversed(items):
                if isinstance(item, dict) and ("result" in item or "error" in item or "id" in item):
                    return item, resp
            return items[-1], resp
        try:
            return resp.json(), resp
        except ValueError as exc:
            raise MCPError(f"MCP {service} returned non-JSON response") from exc

    def _initialize(self, service: str) -> None:
        client = self._client(service)
        _ = client  # ensures state/lock exist
        state = self._states[service]
        if state.initialized:
            return
        with self._locks[service]:
            if state.initialized:
                return
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": self.client_version},
                },
            }
            obj, _resp = self._post(service, payload)
            if isinstance(obj, dict) and obj.get("error"):
                _unwrap_tool_result(obj)
            result = obj.get("result", {}) if isinstance(obj, dict) else {}
            negotiated = result.get("protocolVersion") if isinstance(result, dict) else None
            state.negotiated_protocol = str(negotiated or self.protocol_version)
            notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            try:
                self._post(service, notify, allow_empty=True)
            except MCPError:
                pass
            state.initialized = True

    def invoke(self, tool_name: str, **kwargs: Any) -> Any:
        service = TOOL_TO_SERVICE.get(tool_name)
        if not service:
            raise MCPError(f"unknown MCP tool: {tool_name}")
        self._initialize(service)
        state = self._states[service]
        if state.remote_tools is None:
            try:
                self.list_tools(service)
            except Exception:
                pass
        target_name = tool_name
        prefix = f"mcp_{service}_"
        if state.remote_tools:
            if tool_name in state.remote_tools:
                target_name = tool_name
            elif tool_name.startswith(prefix) and tool_name[len(prefix):] in state.remote_tools:
                target_name = tool_name[len(prefix):]
            elif not tool_name.startswith(prefix) and f"{prefix}{tool_name}" in state.remote_tools:
                target_name = f"{prefix}{tool_name}"
        elif tool_name.startswith(prefix):
            target_name = tool_name
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": target_name, "arguments": kwargs},
        }
        obj, _resp = self._post(service, payload)
        return _unwrap_tool_result(obj)

    def list_tools(self, service: str) -> list[dict[str, Any]]:
        self._initialize(service)
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        obj, _resp = self._post(service, payload)
        result = _unwrap_tool_result(obj)
        tools = result.get("tools") if isinstance(result, dict) and isinstance(result.get("tools"), list) else (result if isinstance(result, list) else [])
        if service in self._states:
            self._states[service].remote_tools = {
                str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")
            }
        return tools

    def probe(self, service: str) -> dict[str, Any]:
        cfg = self._service_cfg(service)
        auth_type = str((cfg.get("auth") or {}).get("type", "none"))
        try:
            tools = self.list_tools(service)
            names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
            advertised = set(names)
            expected = EXPECTED_TOOLS_BY_SERVICE.get(service, set())
            prefix = f"mcp_{service}_"

            def _is_tool_available(t: str) -> bool:
                if t in advertised:
                    return True
                if t.startswith(prefix) and t[len(prefix):] in advertised:
                    return True
                if not t.startswith(prefix) and f"{prefix}{t}" in advertised:
                    return True
                return False

            missing = sorted(t for t in expected if not _is_tool_available(t))
            optional_expected = set(OPTIONAL_INTEL_TOOLS) if service == "intel" else set()
            optional_available = sorted(t for t in optional_expected if _is_tool_available(t))

            def _is_tool_known(n: str) -> bool:
                if n in expected or n in optional_expected:
                    return True
                prefixed = f"{prefix}{n}" if not n.startswith(prefix) else n
                unprefixed = n[len(prefix):] if n.startswith(prefix) else n
                return prefixed in expected or unprefixed in expected or prefixed in optional_expected or unprefixed in optional_expected

            unexpected = sorted(n for n in advertised if not _is_tool_known(n))
            return {
                "ok": True,
                "catalog_match": not missing,
                "service": service,
                "url": self._url(cfg),
                "auth_type": auth_type,
                "session": bool(self._states[service].session_id),
                "protocol_version": self._states[service].negotiated_protocol,
                "tool_count": len(names),
                "expected_tool_count": len(expected),
                "missing_tools": missing,
                "unexpected_tools": unexpected,
                "optional_historical_tools": optional_available,
                "tools": names,
            }
        except Exception as exc:
            return {
                "ok": False,
                "service": service,
                "url": self._url(cfg),
                "auth_type": auth_type,
                "error": str(exc),
            }
