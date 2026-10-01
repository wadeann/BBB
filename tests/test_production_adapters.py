from __future__ import annotations

import base64
import json
import os

import httpx

from a_share_agent.llm.openai_compatible import OpenAICompatibleLLMClient
from a_share_agent.mcp.http import StreamableHTTPMCPInvoker


def test_streamable_http_mcp_basic_auth_and_tool_call(monkeypatch):
    monkeypatch.setenv("TEST_MCP_USER", "demo-user")
    monkeypatch.setenv("TEST_MCP_PASS", "demo-pass")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        expected = "Basic " + base64.b64encode(b"demo-user:demo-pass").decode()
        assert request.headers.get("authorization") == expected
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        headers = {"content-type": "application/json", "mcp-session-id": "session-1"}
        if method == "initialize":
            return httpx.Response(200, headers=headers, json={"jsonrpc":"2.0","id":body["id"],"result":{"protocolVersion":"2025-06-18","capabilities":{},"serverInfo":{"name":"test","version":"1"}}})
        if method == "notifications/initialized":
            return httpx.Response(202, headers=headers)
        if method == "tools/list":
            return httpx.Response(200, headers=headers, json={"jsonrpc":"2.0","id":body["id"],"result":{"tools":[{"name":"mcp_intel_query_data"}]}})
        if method == "tools/call":
            assert request.headers.get("mcp-session-id") == "session-1"
            return httpx.Response(200, headers=headers, json={"jsonrpc":"2.0","id":body["id"],"result":{"structuredContent":{"symbol":"600000.SH","price":10.2}}})
        raise AssertionError(method)

    cfg = {
        "protocol_version":"2025-06-18",
        "services": {
            "intel": {"enabled":True,"url":"https://example.invalid/api/intel","auth":{"type":"basic","username_env":"TEST_MCP_USER","password_env":"TEST_MCP_PASS"}}
        },
    }
    inv = StreamableHTTPMCPInvoker(cfg, transport=httpx.MockTransport(handler))
    assert inv.list_tools("intel")[0]["name"] == "mcp_intel_query_data"
    out = inv.invoke("mcp_intel_query_data", symbol="600000.SH")
    assert out == {"symbol":"600000.SH","price":10.2}
    probe = inv.probe("intel")
    assert probe["ok"] is True
    assert probe["catalog_match"] is False  # test server advertises only one of the intel catalog tools
    assert "mcp_intel_query_batch_data" in probe["missing_tools"]
    assert len(seen) >= 4


def test_openai_compatible_json_client(monkeypatch):
    monkeypatch.setenv("TEST_LLM_URL", "https://llm.invalid")
    monkeypatch.setenv("TEST_LLM_KEY", "secret-key")
    monkeypatch.setenv("TEST_LLM_MODEL", "model-x")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer secret-key"
        body = json.loads(request.content)
        assert body["model"] == "model-x"
        return httpx.Response(200, json={"choices":[{"message":{"content":"{\"ok\": true}"}}]})

    client = OpenAICompatibleLLMClient({
        "base_url_env":"TEST_LLM_URL", "api_key_env":"TEST_LLM_KEY", "model_env":"TEST_LLM_MODEL",
        "structured_output":"json_object",
    }, transport=httpx.MockTransport(handler))
    out = client.complete_json(system_prompt="json", user_payload={"x":1}, schema={"type":"object"})
    assert out == {"ok": True}
