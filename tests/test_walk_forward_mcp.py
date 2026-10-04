from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from a_share_agent.backtest import walk_forward_service as service
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.config import load_config
from a_share_agent import cli


def response():
    return {'source': 'historical_vendor', 'dataset_version': 'v1', 'point_in_time': True,
            'coverage': 1.0, 'coverage_start': '2024-01-01', 'coverage_end': '2024-04-01',
            'data': [{'symbol': 'X', 'effective_from': '2020-01-01', 'effective_to': None}]}


def provider(tmp_path, payload=None, error=None):
    base = tmp_path / 'data/backtest'
    base.mkdir(parents=True)
    for name in ('corporate_actions', 'historical_status_intervals', 'historical_sector_intervals'):
        (base / f'{name}.csv').write_text('symbol,effective_from,effective_to\n')
    calls = []
    def invoke(tool, **kwargs):
        calls.append((tool, kwargs))
        if error:
            raise RuntimeError(error)
        return payload if payload is not None else response()
    return HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=invoke), use_cache=False), calls


def load(p):
    return p.load_universe_for_period('2024-01-01', '2024-04-01', 'missing', mode='strict_point_in_time')


def test_canonical_mcp_response_and_memory_origin(tmp_path):
    payload = response()
    p, calls = provider(tmp_path, payload)
    assert load(p).symbols == ['X']
    snap = p.ledger.snapshot()
    event = next(e for e in snap['events'] if e['source_type'] == 'mcp')
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    assert event['response_json'].encode() == raw
    assert event['sha256'] == hashlib.sha256(raw).hexdigest()
    assert event['tool'] == calls[0][0]
    assert event['service'] == 'intel'
    assert event['dataset_version'] == 'v1'
    assert event['sequence'] in snap['events'][-1]['origin_sequences']
    p.ledger.events[event['sequence'] - 1]['response_json'] = '{}'
    assert snap['status'] == 'VERIFIED'
    assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'


@pytest.mark.parametrize('field', ['dataset_version', 'source', 'point_in_time', 'coverage', 'coverage_end'])
def test_partial_mcp_metadata_unverifiable(tmp_path, field):
    payload = response()
    payload.pop(field)
    p, _ = provider(tmp_path, payload)
    load(p)
    snap = p.ledger.snapshot()
    assert any(e['source_type'] == 'mcp' for e in snap['events'])
    assert snap['status'] == 'UNVERIFIABLE'


def test_mcp_failure_records_no_credentials(tmp_path):
    p, calls = provider(tmp_path, error='password=secret-token http://user:secret-token@localhost')
    with pytest.raises(RuntimeError):
        load(p)
    snap = p.ledger.snapshot()
    assert any(e['source_type'] == 'mcp' and e['status'] == 'error' for e in snap['events'])
    assert 'secret-token' not in json.dumps(snap)
    assert snap['status'] == 'UNVERIFIABLE'
    assert all(tool.startswith('mcp_intel_') for tool, _ in calls)


@pytest.mark.parametrize('backend', [None, 'production'])
def test_cli_selects_backend_without_default_live(tmp_path, monkeypatch, backend):
    (tmp_path / 'wf.yaml').write_text('{}')
    cfg = SimpleNamespace(backtest={}, runtime={'backend': 'fake'})
    monkeypatch.setattr(cli, 'load_config', lambda root: cfg)
    sentinel = object()
    selected = []
    monkeypatch.setattr(cli, 'create_mcp_invoker', lambda cfg, **kw: selected.append(kw['backend']) or sentinel)
    captured = []
    monkeypatch.setattr(cli, 'run_stability', lambda *a, **kw: captured.append(kw) or {'status': 'COMPLETED'})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_walk_forward_stability(SimpleNamespace(root=str(tmp_path), config='wf.yaml', output='out', run_id='r', backend=backend))
    assert exc.value.code == 0
    assert selected == [backend or 'fake']
    assert captured[0]['mcp'] is sentinel


def test_every_fold_receives_mcp(tmp_path, monkeypatch):
    cfg = load_config(__import__('pathlib').Path(__file__).parents[1])
    cfg = replace(cfg, project_root=tmp_path)
    captured = []
    original = service.HistoricalDataProvider
    def make(*args, **kwargs):
        captured.append(kwargs.get('mcp'))
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'HistoricalDataProvider', make)
    mcp = SimpleNamespace(invoke=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    result = service.run_stability(cfg, {'start_date': '2024-01-01', 'end_date': '2024-05-01',
        'train_months': 1, 'test_months': 1, 'step_months': 1, 'warmup_bars': 1,
        'settings': {'universe_mode': 'strict_point_in_time', 'cache': False}}, tmp_path / 'out', mcp=mcp)
    assert len(captured) == result['total_folds'] >= 2
    assert all(item is mcp for item in captured)
    assert result['data_blocked'] == result['total_folds']
    assert result['failed'] == 0
def test_tdx_rows_shape_normalizes_to_bars():
    provider_payload = {
        'Code': '600000',
        'Rows': [{'Data': '20260930', 'Open': '9.22', 'High': '9.49',
                  'Low': '9.16', 'Close': '9.48', 'Volume': '1474848',
                  'Amount': 1386209920}],
    }
    from a_share_agent.backtest.data import normalize_bars
    rows = normalize_bars(provider_payload)
    assert rows == [{'date': '2026-09-30', 'open': 9.22, 'high': 9.49,
                     'low': 9.16, 'close': 9.48, 'volume': 1474848.0,
                     'amount': 1386209920.0, 'pct': 0.0}]
def test_raw_bars_uses_mcp_fetch_kline_when_local_raw_missing(tmp_path):
    payload = {'source': 'historical_vendor', 'dataset_version': 'v1',
               'point_in_time': True, 'coverage': 1.0,
               'coverage_start': '2024-01-01', 'coverage_end': '2024-04-01',
               'klines': [{'time': '2024-01-02', 'open': 1, 'high': 2,
                           'low': 1, 'close': 2, 'volume': 100}]}
    p, calls = provider(tmp_path, payload)
    rows = p.raw_bars('600000.SH', count=20)
    assert rows and rows[0]['date'] == '2024-01-02'
    assert calls[0][0] == 'mcp_intel_fetch_kline'
def test_mcp_bars_requests_full_history_window(tmp_path):
    payload = {'source': 'historical_vendor', 'dataset_version': 'v1',
               'point_in_time': True, 'coverage': 1.0,
               'coverage_start': '2022-01-01', 'coverage_end': '2026-09-30',
               'klines': [{'time': '2024-01-02', 'open': 1, 'high': 2,
                           'low': 1, 'close': 2, 'volume': 100}]}
    p, calls = provider(tmp_path, payload)
    p.bars('600000.SH', count=900)
    assert calls[0][1]['count'] == 1000
def test_bars_falls_back_to_fetch_when_tdx_window_is_short(tmp_path):
    tdx = {'Rows': [{'Data': '2026-01-01', 'Open': 1, 'High': 2,
                     'Low': 1, 'Close': 2, 'Volume': 100}]}
    fetch = {'klines': [{'time': '2022-01-01', 'open': 1, 'high': 2,
                         'low': 1, 'close': 2, 'volume': 100},
                        {'time': '2026-01-01', 'open': 1, 'high': 2,
                         'low': 1, 'close': 2, 'volume': 100}]}
    base = tmp_path / 'data/backtest'
    base.mkdir(parents=True)
    for name in ('corporate_actions', 'historical_status_intervals', 'historical_sector_intervals'):
        (base / f'{name}.csv').write_text('symbol,effective_from,effective_to\n')
    calls = []
    def invoke(tool, **kwargs):
        calls.append(tool)
        return tdx if tool == 'mcp_intel_tdx_kline' else fetch
    p = HistoricalDataProvider(tmp_path, __import__('types').SimpleNamespace(invoke=invoke), use_cache=False)
    rows = p.bars('600000.SH', count=900)
    assert len(rows) == 2
    assert calls == ['mcp_intel_tdx_kline', 'mcp_intel_fetch_kline']
def test_production_mcp_disables_unverified_cache(tmp_path, monkeypatch):
    cfg = load_config(__import__('pathlib').Path(__file__).parents[1])
    cfg = replace(cfg, project_root=tmp_path)
    captured = []
    original = service.HistoricalDataProvider
    def make(*args, **kwargs):
        captured.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'HistoricalDataProvider', make)
    mcp = __import__('types').SimpleNamespace(invoke=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    service.run_stability(cfg, {'start_date': '2024-01-01', 'end_date': '2024-05-01',
        'train_months': 1, 'test_months': 1, 'step_months': 1, 'warmup_bars': 1,
        'settings': {'universe_mode': 'strict_point_in_time', 'cache': True}},
        tmp_path / 'out', mcp=mcp)
    assert captured and all(item['use_cache'] is False for item in captured)
def test_explicit_walk_forward_universe_is_preserved(tmp_path, monkeypatch):
    cfg = load_config(__import__('pathlib').Path(__file__).parents[1])
    cfg = replace(cfg, project_root=tmp_path)
    seen = []
    class Provider:
        def __init__(self, *args, **kwargs): pass
        def load_universe_for_period(self, *args, **kwargs):
            return __import__('types').SimpleNamespace(symbols=['FULL_MARKET'])
    monkeypatch.setattr(service, 'HistoricalDataProvider', Provider)
    monkeypatch.setattr(service, 'settings_from', lambda *a, **k: __import__('types').SimpleNamespace(
        universe_file='u.csv', max_universe=0, universe_mode='configured'))
    def preflight(provider, fold, settings, **kwargs):
        seen.append(fold['universe'])
        return 'DATA_BLOCKED', False, {'blocking': {}}, {}
    monkeypatch.setattr(service, 'preflight_fold', preflight)
    service.run_stability(cfg, {'start_date': '2024-01-01', 'end_date': '2024-05-01',
        'train_months': 1, 'test_months': 1, 'step_months': 1,
        'universe': ['600000.SH', '000001.SZ'], 'settings': {}}, tmp_path / 'out',
        mcp=__import__('types').SimpleNamespace(invoke=lambda *a, **k: {}))
    assert seen and all(symbols == ['600000.SH', '000001.SZ'] for symbols in seen)


def historical_response():
    return {'status': 'ok', 'tool': 'historical_bars', 'source': 'sina',
            'dataset_version': 'sina-local-v1', 'response_hash': 'provider-payload-hash',
            'point_in_time': False, 'coverage_start': '2024-01-02',
            'coverage_end': '2024-01-03', 'coverage': 1.0,
            'adjustment': 'provider_declared', 'returned_rows': 2,
            'bars': {'600000.SH': [
                {'date': '2024-01-03', 'open': 2, 'high': 3, 'low': 1, 'close': 3},
                {'date': '2024-01-02', 'open': 1, 'high': 2, 'low': 1, 'close': 2}]}}


def test_historical_bars_parses_symbol_map_and_records_provenance(tmp_path):
    payload = historical_response()
    p, calls = provider(tmp_path, payload)
    rows = p.historical_bars(['600000.SH', '000001.SZ'], '2024-01-01', '2024-01-04',
                             adjustment='none', max_bars_per_symbol=20)
    assert [row['date'] for row in rows['600000.SH']] == ['2024-01-02', '2024-01-03']
    assert rows['000001.SZ'] == []
    tool, arguments = calls[0]
    assert tool == 'mcp_intel_historical_bars'
    assert arguments == {'symbols': ['600000.SH', '000001.SZ'],
                         'start_date': '2024-01-01', 'end_date': '2024-01-04',
                         'period': 'D', 'adjustment': 'none', 'max_bars_per_symbol': 20}
    event = next(e for e in p.ledger.events if e['source_type'] == 'mcp')
    assert json.loads(event['request_json']) == {'tool': tool, 'arguments': arguments}
    assert event['request_sha256'] == p.ledger.digest(json.loads(event['request_json']))
    assert json.loads(event['response_json']) == payload
    assert event['sha256'] == p.ledger.digest(payload)
    assert event['response_hash'] == payload['response_hash']
    assert event['adjustment'] == 'provider_declared'
    assert event['dataset_version'] == 'sina-local-v1'
    assert event['returned'] == {'row_count': 2, 'date_start': '2024-01-02', 'date_end': '2024-01-03'}
    assert event['sequence'] in p.ledger.events[-1]['origin_sequences']


def test_historical_bars_provider_declared_history_never_verified(tmp_path):
    p, _ = provider(tmp_path, historical_response())
    p.historical_bars(['600000.SH'], '2024-01-02', '2024-01-03')
    event = next(e for e in p.ledger.events if e['source_type'] == 'mcp')
    assert event['dataset_metadata']['point_in_time'] is False
    assert event['verifiable'] is False
    assert p.ledger.verify_mcp_event(event) is False
    assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'


@pytest.mark.parametrize('status', ['partial', 'error'])
def test_historical_bars_records_provider_failure_status_without_fallback(tmp_path, status):
    payload = historical_response()
    payload['status'] = status
    payload['error'] = 'missing provider history'
    if status == 'error':
        payload['bars'] = {}
        payload['returned_rows'] = 0
    p, calls = provider(tmp_path, payload)
    rows = p.historical_bars(['600000.SH'], '2024-01-02', '2024-01-03')
    assert bool(rows['600000.SH']) is (status == 'partial')
    event = next(e for e in p.ledger.events if e['source_type'] == 'mcp')
    assert event['status'] == status
    assert event['verifiable'] is False
    assert len(calls) == 1


def test_http_routes_optional_historical_bars_to_local_canonical_tool():
    import httpx
    from a_share_agent.mcp.http import StreamableHTTPMCPInvoker
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        method = body['method']
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if method == 'initialize':
            result = {'protocolVersion': '2025-06-18', 'capabilities': {}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'historical_bars'}]}
        else:
            result = {'structuredContent': historical_response()}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})
    inv = StreamableHTTPMCPInvoker({'services': {'intel': {'enabled': True, 'url': 'http://local/mcp'}}},
                                  transport=httpx.MockTransport(handle))
    try:
        result = inv.invoke('mcp_intel_historical_bars', symbols=['600000.SH'],
                            start_date='2024-01-02', end_date='2024-01-03',
                            period='D', adjustment='none', max_bars_per_symbol=20)
        assert result == historical_response()
        call = next(body for body in calls if body['method'] == 'tools/call')
        assert call['params']['name'] == 'historical_bars'
        assert call['params']['arguments']['symbols'] == ['600000.SH']
    finally:
        inv.close()
