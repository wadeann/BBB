import hashlib
import json

import pytest

from a_share_agent.backtest.data import HistoricalDataProvider


def provider_with_files(tmp_path):
    base = tmp_path / 'data/backtest'
    base.mkdir(parents=True)
    for name in ('corporate_actions', 'historical_status_intervals', 'historical_sector_intervals'):
        (base / f'{name}.csv').write_text('symbol,effective_from,effective_to\n')
    prices = base / 'prices'
    prices.mkdir()
    path = prices / '000001_SZ.csv'
    path.write_bytes(b'date,open,high,low,close,volume\r\n2024-01-02,1,2,1,2,100\r\n')
    return HistoricalDataProvider(tmp_path, use_cache=False), path


def test_physical_read_commits_exact_consumed_bytes(tmp_path):
    provider, path = provider_with_files(tmp_path)
    provider.bars('000001.SZ', count=42)
    snapshot = provider.ledger.snapshot()
    event = next(e for e in snapshot['events'] if e.get('path') == str(path))
    assert event['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert event['returned']['row_count'] == 1
    assert event['returned']['date_start'] == '2024-01-02'
    assert snapshot['status'] == 'VERIFIED'
    assert snapshot['physical_inputs']


def test_memory_hit_links_original_physical_read(tmp_path):
    provider, path = provider_with_files(tmp_path)
    provider.bars('000001.SZ')
    provider.ledger.phase = 'execution'
    provider.bars('000001.SZ')
    events = provider.ledger.snapshot()['events']
    original = next(e for e in events if e.get('path') == str(path))
    assert events[-1]['source_type'] == 'memory'
    assert original['sequence'] in events[-1]['origin_sequences']
    assert events[-1]['phase'] == 'execution'


def test_missing_input_fails_closed(tmp_path):
    provider, _ = provider_with_files(tmp_path)
    assert provider.raw_bars('MISSING') == []
    assert provider.ledger.snapshot()['status'] == 'UNVERIFIABLE'


def test_changed_consumed_bytes_fail_publication_check(tmp_path):
    provider, path = provider_with_files(tmp_path)
    provider.bars('000001.SZ')
    path.write_text('date,close\n2024-01-02,999\n')
    assert provider.ledger.snapshot(verify_files=True)['status'] == 'UNVERIFIABLE'


def test_unbound_disk_cache_is_not_authentic_provenance(tmp_path):
    provider, _ = provider_with_files(tmp_path)
    provider.use_cache = True
    cache = provider._cache_file('bars', '000001.SZ')
    cache.write_text(json.dumps([{'date': '2024-01-02', 'close': 2}]))
    provider.bars('000001.SZ')
    snapshot = provider.ledger.snapshot()
    assert any(e['source_type'] == 'cache' for e in snapshot['events'])
    assert snapshot['status'] == 'UNVERIFIABLE'


def test_symlink_input_rejected(tmp_path):
    provider, path = provider_with_files(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + '-outside.csv')
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink|escape'):
        provider.bars('000001.SZ')
    assert provider.ledger.snapshot()['status'] == 'UNVERIFIABLE'


def test_artifact_binds_ledger_hash():
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    artifact = build_per_key_oos_artifact({}, 'r', 's', 'c', 'w', {},
        run_manifest={'consumed_input_ledger_sha256': 'a' * 64})
    assert artifact['input_manifest_binding']['consumed_input_ledger_sha256'] == 'a' * 64


def test_injected_memory_bars_cannot_borrow_context_provenance(tmp_path):
    provider, _ = provider_with_files(tmp_path)
    provider._bars_mem['INJECTED'] = [{'date': '2024-01-02', 'close': 999}]
    provider.bars('INJECTED')
    assert provider.ledger.snapshot()['status'] == 'UNVERIFIABLE'


def test_memory_hit_different_count_links_actual_original(tmp_path):
    provider, path = provider_with_files(tmp_path)
    provider.bars('000001.SZ', count=42)
    provider.bars('000001.SZ', count=900)
    events = provider.ledger.snapshot()['events']
    original = next(e for e in events if e.get('path') == str(path))
    assert events[-1]['origin_sequences'] == [original['sequence']]


def test_daily_universe_memory_links_security_master(tmp_path):
    provider, _ = provider_with_files(tmp_path)
    path = tmp_path / 'data/backtest/security_master.csv'
    path.write_text('symbol,listing_date,industry_code\n000001.SZ,2020-01-01,TECH\n')
    provider.load_universe_for_period('2024-01-01', '2024-02-01', 'unused', mode='strict_point_in_time')
    provider.active_records_on('2024-01-02')
    events = provider.ledger.snapshot()['events']
    original = next(e for e in events if e.get('path') == str(path))
    assert original['sequence'] in events[-1]['origin_sequences']


def test_read_event_carries_actual_symbol_request(tmp_path):
    provider, path = provider_with_files(tmp_path)
    provider.bars('000001.SZ', count=42)
    event = next(e for e in provider.ledger.snapshot()['events'] if e.get('path') == str(path))
    assert event['requested_range'] == {'args': ['000001.SZ'], 'kwargs': {'count': 42}}
