import pytest

from a_share_agent.runtime import AgentRuntime
from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.execution.engine import ExecutionRejected


def test_paper_execution_idempotent(runtime_root):
    rt=AgentRuntime(runtime_root, FakeMCPInvoker())
    signal={"symbol":"600000.SH","direction":"BUY","strategy_id":"ma60_breakout_retest","signal_version":"pytest-v1","limit_price":10.0,"stop":9.6,"quantity":1000,"reason":"test"}
    route={"route_id":"RISK_ON_STRONG_SECTOR","position_multiplier":1.0}
    out=rt.execute_signal("ENTRY_WINDOW_AM", signal, route)
    assert out["receipt"]["status"]=="FILLED"
    with pytest.raises(ExecutionRejected):
        rt.execute_signal("ENTRY_WINDOW_AM", signal, route)
