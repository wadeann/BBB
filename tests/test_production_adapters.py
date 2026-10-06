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


def test_openai_decision_schema_compat_normalizes_root_and_confidence(monkeypatch):
    monkeypatch.setenv("TEST_LLM_URL", "https://llm.invalid")
    monkeypatch.setenv("TEST_LLM_KEY", "secret-key")
    monkeypatch.setenv("TEST_LLM_MODEL", "model-x")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        content = json.dumps({"candidates":[{
            "candidate_id":"abc", "decision":"PASS", "confidence":0.85,
            "reasons_for":["ok"], "reasons_against":["risk"], "risk_flags":[]
        }]})
        return httpx.Response(200, json={"choices":[{"message":{"content":content}}]})

    schema={
        "type":"object",
        "properties":{"decisions":{"type":"array","items":{"type":"object","properties":{
            "candidate_id":{"type":"string"},"decision":{"type":"string"},"confidence":{"type":"number"},
            "reasons_for":{"type":"array"},"reasons_against":{"type":"array"},"risk_flags":{"type":"array"}},
            "required":["candidate_id","decision","confidence","reasons_for","reasons_against","risk_flags"],"additionalProperties":False}}},
        "required":["decisions"],"additionalProperties":False,
    }
    client=OpenAICompatibleLLMClient({
        "base_url_env":"TEST_LLM_URL","api_key_env":"TEST_LLM_KEY","model_env":"TEST_LLM_MODEL",
        "structured_output":"json_schema",
    },transport=httpx.MockTransport(handler))
    out=client.complete_json(system_prompt="json",user_payload={"x":1},schema=schema)
    assert out["decisions"][0]["confidence"] == 85.0
def _wire_invoker_response(wire: bytes, *, content_type="application/json", status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=wire, headers={"content-type": content_type})

    return StreamableHTTPMCPInvoker(
        {"services": {"intel": {"enabled": True, "url": "http://local/mcp"}}},
        transport=httpx.MockTransport(handler),
    )


def test_mcp_wire_commitment_uses_exact_response_bytes_and_metadata():
    wire = b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}'
    inv = _wire_invoker_response(wire)
    try:
        assert inv._post("intel", {"jsonrpc": "2.0", "id": 1, "method": "x"})[0]["result"]
        envelope = inv.last_response_envelope
        assert envelope.wire_bytes == wire
        assert envelope.wire_sha256 == __import__("hashlib").sha256(wire).hexdigest()
        assert envelope.wire_byte_count == len(wire)
        assert envelope.status_code == 200
        assert envelope.content_type == "application/json"
        assert envelope.transport == "streamable_http"
    finally:
        inv.close()


def test_mcp_wire_hash_changes_for_json_formatting_and_sse_framing():
    json_a = b'{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}'
    json_b = b'{ "jsonrpc": "2.0", "id": 1, "result": { "structuredContent": { "ok": true } } }'
    inv_a = _wire_invoker_response(json_a)
    inv_b = _wire_invoker_response(json_b)
    try:
        parsed_a, _ = inv_a._post("intel", {"id": 1})
        parsed_b, _ = inv_b._post("intel", {"id": 1})
        assert parsed_a == parsed_b
        assert inv_a.last_response_envelope.wire_sha256 != inv_b.last_response_envelope.wire_sha256
    finally:
        inv_a.close()
        inv_b.close()

    sse_a = b'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}\n\n'
    sse_b = b': keepalive\n\ndata: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}\n\n'
    sse_inv_a = _wire_invoker_response(sse_a, content_type="text/event-stream")
    sse_inv_b = _wire_invoker_response(sse_b, content_type="text/event-stream")
    try:
        parsed_a, _ = sse_inv_a._post("intel", {"id": 1})
        parsed_b, _ = sse_inv_b._post("intel", {"id": 1})
        assert parsed_a == parsed_b
        assert sse_inv_a.last_response_envelope.wire_sha256 != sse_inv_b.last_response_envelope.wire_sha256
    finally:
        sse_inv_a.close()
        sse_inv_b.close()


def test_mcp_wire_metadata_missing_or_malformed_is_not_usable():
    wire = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    inv = _wire_invoker_response(wire, content_type="")
    try:
        inv._post("intel", {"id": 1})
        envelope = inv.last_response_envelope
        assert envelope.content_type == ""
        assert not envelope.is_valid
    finally:
        inv.close()


def _bound_wire_invoker(*, status=200, sse=False):
    def handler(request):
        payload = json.loads(request.content)
        if payload['method'] == 'notifications/initialized':
            return httpx.Response(202)
        if payload['method'] == 'initialize':
            result = {'protocolVersion': '2025-06-18'}
        elif payload['method'] == 'tools/list':
            result = {'tools': []}
        else:
            result = {'structuredContent': {'symbol': payload['params']['arguments']['symbol']}}
        wire = json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': result}).encode()
        if sse:
            wire = b': heartbeat\n\ndata: ' + wire + b'\n\n'
        return httpx.Response(status if payload['method'] == 'tools/call' else 200,
                              content=wire, headers={'content-type': 'text/event-stream' if sse else 'application/json'})
    return StreamableHTTPMCPInvoker(
        {'services': {'intel': {'enabled': True, 'url': 'http://local/mcp'}}},
        transport=httpx.MockTransport(handler))


def test_invocation_evidence_is_bound_to_exact_request_and_concurrent_result():
    from concurrent.futures import ThreadPoolExecutor
    inv = _bound_wire_invoker()
    try:
        inv.list_tools('intel')
        with ThreadPoolExecutor(max_workers=4) as pool:
            calls = list(pool.map(lambda symbol: inv.invoke_with_evidence('mcp_intel_tdx_kline', symbol=symbol), ['A', 'B', 'C', 'D']))
        for result, evidence in calls:
            request = json.loads(evidence.request_bytes)
            wire = json.loads(evidence.wire_bytes)
            assert request['method'] == 'tools/call'
            assert request['params']['arguments']['symbol'] == result['symbol']
            assert request['id'] == wire['id']
        assert inv.invoke('mcp_intel_tdx_kline', symbol='ordinary') == {'symbol': 'ordinary'}
    finally:
        inv.close()


def test_http_error_retains_invocation_evidence_without_headers():
    import pytest
    from a_share_agent.mcp.base import MCPError
    inv = _bound_wire_invoker(status=503)
    try:
        with pytest.raises(MCPError) as caught:
            inv.invoke_with_evidence('mcp_intel_tdx_kline', symbol='X')
        envelope = caught.value.response_envelope
        assert envelope.status_code == 503
        assert json.loads(envelope.request_bytes)['params']['arguments'] == {'symbol': 'X'}
        assert envelope.wire_bytes
        assert not hasattr(envelope, 'headers')
    finally:
        inv.close()
