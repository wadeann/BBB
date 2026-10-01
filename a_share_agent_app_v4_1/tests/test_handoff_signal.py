from a_share_agent.config import load_config
from a_share_agent.core.schema_validator import SchemaRegistry
from a_share_agent.core.permissions import PhasePermissions
from a_share_agent.core.handoff import HandoffContract
from a_share_agent.strategy.signal_engine import DeterministicSignalEngine


def test_handoff_contract(runtime_root):
    cfg=load_config(runtime_root)
    c=HandoffContract(SchemaRegistry(runtime_root/"skill"/"schemas"), PhasePermissions(cfg.permissions))
    env=c.build(message_type="RISK_RESULT",run_id="r",trace_id="t",phase="ENTRY_WINDOW_AM",producer={"type":"risk","id":"x"},payload={"status":"PASS"},next_action="ORDER_REQUEST",freshness_seconds=60)
    c.validate_for_consumer(env,expected_phase="ENTRY_WINDOW_AM",consume_idempotency=True)


def test_signal_engine_returns_list():
    bars=[]
    for i in range(100):
        base=10+i*.02
        bars.append({"open":base-.02,"high":base+.05,"low":base-.05,"close":base,"volume":1000000+i*1000})
    out=DeterministicSignalEngine().scan(bars,market_regime="risk_on",sector_strength="strong")
    assert isinstance(out,list)
