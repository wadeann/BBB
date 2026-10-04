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
    assert not ConsumedInputLedger.verify_mcp_event(event)


def test_empty_mcp_response_recorded(tmp_path):
    p = HistoricalDataProvider(tmp_path, SimpleNamespace(invoke=lambda *a, **k: {'data': []}))
    p.mcp.invoke('mcp_intel_get_historical_universe')
    assert p.ledger.events[-1]['status'] == 'empty'
    assert p.ledger.snapshot()['status'] == 'UNVERIFIABLE'
