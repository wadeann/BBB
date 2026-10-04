from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from a_share_agent.backtest import walk_forward_service as service
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.walk_forward_preflight import _validate_symbol_data
from a_share_agent.config import load_config


@pytest.mark.parametrize('start,warmup', [('2024-10-01', 260), ('2021-06-01', 17), ('2027-02-01', 400)])
def test_fold_acquisition_uses_calendar_and_configured_warmup(tmp_path, monkeypatch, start, warmup):
    calendar = []
    day = date.fromisoformat(start) - timedelta(days=warmup * 2 + 60)
    end = date.fromisoformat(start) + timedelta(days=100)
    while day < end + timedelta(days=120):
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    base = tmp_path / 'data/backtest'
    base.mkdir(parents=True)
    (base / 'trading_grade_calendar.csv').write_text('date\n' + '\n'.join(calendar) + '\n')
    cfg = replace(load_config(__import__('pathlib').Path(__file__).parents[1]), project_root=tmp_path)
    captured, folds = [], []
    original = service.HistoricalDataProvider
    def make(*args, **kwargs):
        captured.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'HistoricalDataProvider', make)
    def preflight(provider, fold, settings, **kwargs):
        folds.append((dict(fold), settings.warmup_bars))
        return 'DATA_BLOCKED', False, {'blocking': {'fixture': 'blocked'}}, {}
    monkeypatch.setattr(service, 'preflight_fold', preflight)
    service.run_stability(cfg, {'start_date': start, 'end_date': end.isoformat(),
        'train_months': 1, 'test_months': 1, 'step_months': 1,
        'warmup_bars': warmup, 'universe': ['600000.SH'], 'settings': {}},
        tmp_path / 'out', mcp=SimpleNamespace(invoke=lambda *a, **k: {}))
    assert folds
    for request, (fold, required) in zip(captured, folds):
        preceding = [d for d in calendar if d < fold['train_start']]
        assert fold['warmup_start'] == preceding[-warmup]
        assert required == warmup
        tail = [d for d in calendar if d >= fold['test_end_exclusive']]
        # Next-open entry, N holding sessions, then next-open exit.
        expected_end = (date.fromisoformat(tail[21]) + timedelta(days=1)).isoformat()
        assert fold['observation_end_exclusive'] == expected_end
        assert expected_end > fold['test_end_exclusive']
        assert request['historical_range'] == (preceding[-warmup], expected_end)
        assert request['historical_request_end'] == tail[21]
        assert request['historical_max_bars'] == len([d for d in calendar
            if preceding[-warmup] <= d < expected_end])
        assert request['use_cache'] is False
        assert request['strict_paid_source'] is True


def historical_provider(tmp_path, payload, **options):
    calls = []
    def invoke(tool, **kwargs):
        calls.append((tool, kwargs))
        return payload
    provider = HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=invoke),
        use_cache=False, strict_paid_source=True,
        historical_range=('2024-01-01', '2024-03-01'), **options)
    return provider, calls


def payload(adjustment='qfq'):
    return {'status': 'ok', 'source': 'tdx', 'adjustment': adjustment,
        'adjustment_semantics': {'mode': 'qfq', 'point_in_time': True, 'as_of': '2024-02-29'},
        'adjustment_factors': {'600000.SH': {'2024-01-02': {'factor': 0.5,
            'as_of': '2024-01-02', 'dataset_version': 'factor-v1', 'point_in_time': True}}},
        'bars': {'600000.SH': [{'date': '2024-01-02', 'open': 1, 'high': 2, 'low': 1, 'close': 2}]}}


def test_signal_acquisition_requests_explicit_qfq_and_fold_capacity(tmp_path):
    p, calls = historical_provider(tmp_path, payload(), historical_max_bars=1273)
    assert p.bars('600000.SH')
    assert p.bars('600000.SH')
    assert len(calls) == 1
    assert calls[0][1]['adjustment'] == 'qfq'
    assert calls[0][1]['max_bars_per_symbol'] == 1273
    assert calls[0][1]['provider'] == 'tdx'
    assert calls[0][1]['strict_paid_source'] is True


@pytest.mark.parametrize('field', ['adjustment_factors', 'adjustment_semantics'])
def test_strict_adjusted_history_rejects_missing_factor_evidence(tmp_path, field):
    response = payload()
    del response[field]
    p, _ = historical_provider(tmp_path, response)
    with pytest.raises(RuntimeError, match='adjustment evidence'):
        p.bars('600000.SH')


@pytest.mark.parametrize('mutation', ['future_as_of', 'missing_date', 'nonfinite', 'ambiguous', 'hfq'])
def test_strict_adjusted_history_rejects_unusable_factors(tmp_path, mutation):
    response = payload()
    if mutation == 'future_as_of':
        response['adjustment_semantics']['as_of'] = '2026-01-01'
    elif mutation == 'missing_date':
        response['adjustment_factors']['600000.SH'] = {}
    elif mutation == 'nonfinite':
        response['adjustment_factors']['600000.SH']['2024-01-02']['factor'] = 'nan'
    elif mutation == 'ambiguous':
        response['adjustment_semantics']['point_in_time'] = False
    else:
        response['adjustment'] = 'hfq'
    p, _ = historical_provider(tmp_path, response)
    with pytest.raises(RuntimeError, match='adjustment'):
        p.bars('600000.SH')


@pytest.mark.parametrize('method,adjustment', [('bars', 'qfq'), ('raw_bars', 'none')])
@pytest.mark.parametrize('leaked_date', ['2023-12-31', '2024-03-01', '2026-01-01'])
def test_historical_acquisition_rejects_out_of_window_rows(tmp_path, method, adjustment, leaked_date):
    response = payload(adjustment)
    response['bars']['600000.SH'][0]['date'] = leaked_date
    p, _ = historical_provider(tmp_path, response)
    with pytest.raises(RuntimeError, match='outside requested historical range'):
        getattr(p, method)('600000.SH')


def test_raw_history_does_not_require_adjustment_factors(tmp_path):
    response = payload('none')
    del response['adjustment_factors']
    del response['adjustment_semantics']
    p, calls = historical_provider(tmp_path, response)
    assert p.raw_bars('600000.SH')
    assert calls[0][1]['adjustment'] == 'none'


def test_incomplete_actual_warmup_bars_cannot_be_ready():
    dates = ['2024-01-02', '2024-01-03', '2024-01-04']
    row = {'date': dates[0], 'open': 1, 'high': 2, 'low': 1, 'close': 2}
    p = SimpleNamespace(bars=lambda symbol: [row], raw_bars=lambda symbol: [row])
    result = _validate_symbol_data('600000.SH', p, dates[0], '2024-02-01',
        '2024-02-01', '2024-03-01', '2024-03-01', '2024-04-01', '2024-04-01', 3, set(dates))
    assert result['ok'] is False
    assert result['warmup_adj_bars'] == result['warmup_raw_bars'] == 1
    assert any('INSUFFICIENT_WARMUP_ADJ:1_lt_3' in issue for issue in result['issues'])
    assert any('INSUFFICIENT_WARMUP_RAW:1_lt_3' in issue for issue in result['issues'])


def test_missing_optional_historical_tool_is_explicit_data_blocker(tmp_path, monkeypatch):
    cfg = replace(load_config(__import__('pathlib').Path(__file__).parents[1]), project_root=tmp_path)
    calls = []
    mcp = SimpleNamespace(list_tools=lambda service: [{'name': 'tdx_kline'}],
                          invoke=lambda *a, **k: calls.append((a, k)))
    def preflight(provider, fold, settings, **kwargs):
        provider.bars('600000.SH')
        return 'READY', True, {}, {}
    monkeypatch.setattr(service, 'preflight_fold', preflight)
    result = service.run_stability(cfg, {'start_date': '2024-10-01', 'end_date': '2025-01-01',
        'train_months': 1, 'test_months': 1, 'step_months': 1, 'warmup_bars': 260,
        'universe': ['600000.SH'], 'settings': {}}, tmp_path / 'out', mcp=mcp)
    assert result['data_blocked'] == result['total_folds']
    assert result['failed'] == 0
    reports = list((tmp_path / 'out/folds').glob('*/report.json'))
    assert reports
    assert all('HISTORICAL_TOOL_UNAVAILABLE:mcp_intel_historical_bars' in report.read_text()
               for report in reports)
    assert calls == []


@pytest.mark.parametrize('mutation', ['future', 'missing_version', 'not_pit', 'scalar'])
def test_adjustment_factor_must_be_point_in_time_for_each_bar(tmp_path, mutation):
    response = payload()
    factor = response['adjustment_factors']['600000.SH']['2024-01-02']
    if mutation == 'future':
        factor['as_of'] = '2024-02-29'
    elif mutation == 'missing_version':
        del factor['dataset_version']
    elif mutation == 'not_pit':
        factor['point_in_time'] = False
    else:
        response['adjustment_factors']['600000.SH']['2024-01-02'] = 0.5
    p, _ = historical_provider(tmp_path, response)
    with pytest.raises(RuntimeError, match='adjustment evidence'):
        p.bars('600000.SH')


def test_diagnostics_include_preflight_blockers_for_every_fold(tmp_path, monkeypatch):
    cfg = replace(load_config(__import__('pathlib').Path(__file__).parents[1]), project_root=tmp_path)
    monkeypatch.setattr(service, 'preflight_fold', lambda *a, **k: (
        'DATA_BLOCKED', False, {'blocking': {'warmup': 'ACTUAL_BARS:215_lt_260'}}, {}))
    result = service.run_stability(cfg, {'start_date': '2024-10-01', 'end_date': '2025-01-01',
        'train_months': 1, 'test_months': 1, 'step_months': 1, 'warmup_bars': 260,
        'universe': ['600000.SH'], 'settings': {}}, tmp_path / 'out')
    import json
    diagnostics = json.loads((tmp_path / 'out/diagnostics.json').read_text())
    assert len(diagnostics['fold_errors']) == result['total_folds']
    assert all(error['status'] == 'DATA_BLOCKED' for error in diagnostics['fold_errors'])
    assert all(error['preflight']['reasons']['blocking']['warmup'] == 'ACTUAL_BARS:215_lt_260'
               for error in diagnostics['fold_errors'])
