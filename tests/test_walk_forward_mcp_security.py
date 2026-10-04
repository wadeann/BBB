import json
from types import SimpleNamespace

import pytest

from a_share_agent.backtest.data import ConsumedInputLedger, HistoricalDataProvider


@pytest.mark.parametrize('tool', ['mcp_exec_buy', 'mcp_risk_check', 'mcp_intel_wencai_search', 'mcp_intel_tdx_f10'])
def test_read_only_rejects_nonhistorical_tools_before_transport(tmp_path, tool):
    calls = []
    p = HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=lambda *a, **k: calls.append(a)), read_only_mcp=True)
    with pytest.raises(RuntimeError, match='denied'):
        p.mcp.invoke(tool)
    assert calls == []


def test_response_credentials_redacted_and_never_verified(tmp_path):
    raw = {'password': 'secret-token', 'data': [{'symbol': 'X'}]}
    p = HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=lambda *a, **k: raw))
    assert p.mcp.invoke('mcp_intel_get_historical_universe') is raw
    event = p.ledger.events[-1]
    assert 'secret-token' not in json.dumps(p.ledger.snapshot())
    assert event['redacted'] is True
    assert not p.ledger.verify_mcp_event(event)


def test_empty_mcp_response_recorded(tmp_path):
    p = HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=lambda *a, **k: {'data': []}))
    p.mcp.invoke('mcp_intel_get_historical_universe')
    assert p.ledger.events[-1]['status'] == 'empty'
    assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'

def test_strict_paid_source_never_calls_free_kline_fallback(tmp_path):
    calls = []

    def invoke(tool, **kwargs):
        calls.append(tool)
        if tool == "mcp_intel_tdx_kline":
            return {"Rows": []}
        return {"klines": [{"time": "2024-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}]}

    provider = HistoricalDataProvider(
        tmp_path, SimpleNamespace(invoke=invoke), use_cache=False,
        read_only_mcp=True, strict_paid_source=True,
    )
    assert provider.bars("000001.SZ", count=10) == []
    assert calls == ["mcp_intel_tdx_kline"]


def _evidence_provider(root, *, sse=False, historical=False, status=200):
    import httpx
    from a_share_agent.mcp.http import StreamableHTTPMCPInvoker
    payload = {'dataset_version': 'v1', 'source': 'paid_history', 'point_in_time': True,
               'coverage': 1.0, 'coverage_start': '2024-01-01', 'coverage_end': '2024-01-31',
               'data': [{'symbol': 'X'}]}
    if historical:
        payload.update(status='success', adjustment='none',
                       coverage={'requested_start': '2024-01-01', 'requested_end': '2024-01-31',
                                 'status': 'complete', 'complete': True,
                                 'symbols': {'X': {'start': '2024-01-02', 'end': '2024-01-02', 'rows': 1, 'complete': True}}},
                       returned_rows={'X': 1}, bars={'X': [{'time': '2024-01-02', 'close': 1}]})
    def handler(request):
        rpc = json.loads(request.content)
        method = rpc['method']
        if method == 'notifications/initialized':
            return httpx.Response(202)
        result = ({'protocolVersion': '2025-06-18'} if method == 'initialize' else
                  {'tools': []} if method == 'tools/list' else {'structuredContent': payload})
        wire = json.dumps({'jsonrpc': '2.0', 'id': rpc['id'], 'result': result}).encode()
        if sse:
            wire = b': keepalive\n\ndata: ' + wire + b'\n\n'
        return httpx.Response(status if method == 'tools/call' else 200, content=wire,
                              headers={'content-type': 'text/event-stream' if sse else 'application/json'})
    inv = StreamableHTTPMCPInvoker({'services': {'intel': {'enabled': True, 'url': 'http://local/mcp'}}},
                                   transport=httpx.MockTransport(handler))
    base = root / 'data/backtest'
    base.mkdir(parents=True)
    for name in ('corporate_actions', 'historical_status_intervals', 'historical_sector_intervals'):
        (base / f'{name}.csv').write_text('symbol,effective_from,effective_to\n')
    provider = HistoricalDataProvider(root, inv, use_cache=False)
    return provider, payload


@pytest.mark.parametrize('sse', [False, True])
def test_complete_bound_wire_evidence_is_verified(tmp_path, sse):
    p, payload = _evidence_provider(tmp_path, sse=sse)
    try:
        assert p.mcp.invoke('mcp_intel_get_historical_universe', start_date='2024-01-01', end_date='2024-01-31') == payload
        assert p.ledger.snapshot()['status'] == 'VERIFIED'
        artifact = tmp_path / p.ledger.events[-1]['wire_artifact_path']
        assert artifact.stat().st_mode & 0o777 == 0o400
        assert artifact.parent.stat().st_mode & 0o777 == 0o700
    finally:
        p.mcp.invoker.close()


@pytest.mark.parametrize('mutation', ['parsed', 'id', 'tool', 'arguments', 'external', 'symlink', 'status', 'rpc_error'])
def test_wire_and_trusted_root_mismatches_fail_closed(tmp_path, mutation):
    import hashlib
    from pathlib import Path
    p, _ = _evidence_provider(tmp_path / 'root')
    try:
        p.mcp.invoke('mcp_intel_get_historical_universe', start_date='2024-01-01', end_date='2024-01-31')
        event = p.ledger.events[-1]
        artifact = p.ledger.root / event['wire_artifact_path']
        wire = artifact.read_bytes()
        if mutation == 'parsed':
            response = json.loads(event['response_json'])
            response['data'] = [{'symbol': 'FORGED'}]
            event['response_json'] = json.dumps(response)
            event['response_json_sha256'] = p.ledger.digest(response)
        elif mutation in {'id', 'rpc_error'}:
            rpc = json.loads(wire)
            if mutation == 'id':
                rpc['id'] += 100
            else:
                rpc.pop('result')
                rpc['error'] = {'code': -1, 'message': 'failed'}
            wire = json.dumps(rpc).encode()
            artifact.chmod(0o600)
            artifact.write_bytes(wire)
            event['wire_sha256'] = hashlib.sha256(wire).hexdigest()
            event['wire_byte_count'] = len(wire)
        elif mutation in {'tool', 'arguments'}:
            request = json.loads(event['request_json'])
            if mutation == 'tool':
                request['tool'] = event['tool'] = 'mcp_intel_get_historical_security'
            else:
                request['arguments']['start_date'] = '2024-01-02'
            event['request_json'] = json.dumps(request)
            event['request_sha256'] = p.ledger.digest(request)
        elif mutation in {'external', 'symlink'}:
            outside = tmp_path / 'outside.bin'
            outside.write_bytes(wire)
            if mutation == 'external':
                event['wire_artifact_root'] = str(tmp_path)
                event['wire_artifact_path'] = str(outside)
            else:
                link = p.ledger.root / 'link.bin'
                link.symlink_to(outside)
                event['wire_artifact_path'] = 'link.bin'
        else:
            event['wire_status_code'] = 503
        assert not p.ledger.verify_mcp_event(event)
        assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'
    finally:
        p.mcp.invoker.close()


def test_complete_historical_success_dict_coverage_is_verified(tmp_path):
    p, _ = _evidence_provider(tmp_path, historical=True)
    try:
        p.mcp.invoke('mcp_intel_historical_bars', symbols=['X'], start_date='2024-01-01', end_date='2024-01-31')
        assert p.ledger.events[-1]['status'] == 'ok'
        assert p.ledger.snapshot()['status'] == 'VERIFIED'
    finally:
        p.mcp.invoker.close()


def test_captured_http_error_artifact_is_unverifiable(tmp_path):
    p, _ = _evidence_provider(tmp_path, status=503)
    try:
        with pytest.raises(RuntimeError):
            p.mcp.invoke('mcp_intel_get_historical_universe')
        event = p.ledger.events[-1]
        assert event['status'] == 'error'
        assert event['wire_status_code'] == 503
        assert (tmp_path / event['wire_artifact_path']).read_bytes()
        assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'
    finally:
        p.mcp.invoker.close()


def test_legacy_global_envelope_cannot_promote_mock(tmp_path):
    from a_share_agent.mcp.http import MCPResponseEnvelope
    payload = {'data': [{'symbol': 'X'}], 'source': 'vendor', 'dataset_version': 'v1',
               'point_in_time': True, 'coverage': 1.0, 'coverage_start': '2024-01-01', 'coverage_end': '2024-01-31'}
    wire = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'structuredContent': payload}}).encode()
    inv = SimpleNamespace(invoke=lambda *a, **k: payload,
                          last_response_envelope=MCPResponseEnvelope(wire, 200, 'application/json'))
    p = HistoricalDataProvider(tmp_path, inv)
    p.mcp.invoke('mcp_intel_get_historical_universe')
    assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'


@pytest.mark.parametrize('field', ['dataset_version', 'point_in_time', 'source', 'complete', 'rows', 'symbols', 'status'])
def test_historical_dict_coverage_partial_or_missing_metadata_is_unverifiable(tmp_path, field):
    p, payload = _evidence_provider(tmp_path, historical=True)
    if field in {'dataset_version', 'point_in_time', 'source'}:
        payload.pop(field)
    elif field == 'complete':
        payload['coverage']['symbols']['X']['complete'] = False
    elif field == 'rows':
        payload['coverage']['symbols']['X']['rows'] = 2
    elif field == 'symbols':
        payload['coverage']['symbols']['OTHER'] = payload['coverage']['symbols'].pop('X')
    else:
        payload['status'] = 'partial'
    try:
        p.mcp.invoke('mcp_intel_historical_bars', symbols=['X'], start_date='2024-01-01', end_date='2024-01-31')
        assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'
    finally:
        p.mcp.invoker.close()


def test_cross_call_envelope_mixup_is_unverifiable(tmp_path):
    p, _ = _evidence_provider(tmp_path / 'capture')
    inv = p.mcp.invoker
    try:
        _first, first_evidence = inv.invoke_with_evidence('mcp_intel_get_historical_universe', date='2024-01-01')
        second, _second_evidence = inv.invoke_with_evidence('mcp_intel_get_historical_universe', date='2024-01-02')
        mixed = SimpleNamespace(invoke_with_evidence=lambda *a, **k: (second, first_evidence))
        consumer = HistoricalDataProvider(tmp_path / 'consumer', mixed)
        consumer.mcp.invoke('mcp_intel_get_historical_universe', date='2024-01-02')
        assert not consumer.ledger.verify_mcp_event(consumer.ledger.events[-1])
    finally:
        inv.close()


@pytest.mark.parametrize('status', [200, 503])
def test_sensitive_error_wire_is_not_persisted(tmp_path, status):
    import httpx
    from a_share_agent.mcp.http import StreamableHTTPMCPInvoker
    def handler(request):
        rpc = json.loads(request.content)
        if rpc['method'] == 'notifications/initialized':
            return httpx.Response(202)
        result = {'tools': []} if rpc['method'] == 'tools/list' else {'protocolVersion': '2025-06-18'}
        body = {'jsonrpc': '2.0', 'id': rpc['id'], 'result': result}
        if rpc['method'] == 'tools/call':
            body = {'jsonrpc': '2.0', 'id': rpc['id'], 'error': {'code': -1, 'message': 'failed', 'data': {'password': 'secret-token'}}}
        return httpx.Response(status if rpc['method'] == 'tools/call' else 200,
                              json=body, headers={'content-type': 'application/json'})
    inv = StreamableHTTPMCPInvoker({'services': {'intel': {'enabled': True, 'url': 'http://local/mcp'}}},
                                   transport=httpx.MockTransport(handler))
    p = HistoricalDataProvider(tmp_path, inv)
    try:
        with pytest.raises(RuntimeError):
            p.mcp.invoke('mcp_intel_get_historical_universe')
        event = p.ledger.events[-1]
        assert event['status'] == 'error'
        assert event['wire_artifact_path'] is None
        assert event['redacted'] is True
        assert 'secret-token' not in json.dumps(p.ledger.snapshot())
        assert not list((tmp_path / 'data/backtest').glob('mcp_wire/*.bin'))
    finally:
        inv.close()


def test_wire_boolean_numeric_type_substitution_fails_closed(tmp_path):
    p, payload = _evidence_provider(tmp_path)
    payload['point_in_time'] = 1
    try:
        p.mcp.invoke('mcp_intel_get_historical_universe')
        event = p.ledger.events[-1]
        forged = json.loads(event['response_json'])
        forged['point_in_time'] = True
        event['response_json'] = json.dumps(forged)
        event['response_json_sha256'] = p.ledger.digest(forged)
        event['dataset_metadata']['point_in_time'] = True
        assert not p.ledger.verify_mcp_event(event)
    finally:
        p.mcp.invoker.close()


def test_mutable_request_text_cannot_replace_captured_request(tmp_path):
    p, _ = _evidence_provider(tmp_path)
    try:
        p.mcp.invoke('mcp_intel_get_historical_universe', date='2024-01-01')
        event = p.ledger.events[-1]
        request = json.loads(event['request_json'])
        request['arguments']['date'] = '2024-01-02'
        event['request_json'] = json.dumps(request)
        event['request_sha256'] = p.ledger.digest(request)
        wire_request = json.loads(event['wire_request_json'])
        wire_request['params']['arguments']['date'] = '2024-01-02'
        event['wire_request_json'] = json.dumps(wire_request)
        assert not p.ledger.verify_mcp_event(event)
    finally:
        p.mcp.invoker.close()


def test_wire_metadata_boolean_numeric_substitution_fails_closed(tmp_path):
    p, payload = _evidence_provider(tmp_path)
    payload['point_in_time'] = 1
    try:
        p.mcp.invoke('mcp_intel_get_historical_universe')
        event = p.ledger.events[-1]
        event['dataset_metadata']['point_in_time'] = True
        event['verifiable'] = True
        assert not p.ledger.verify_mcp_event(event)
        assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'
    finally:
        p.mcp.invoker.close()
