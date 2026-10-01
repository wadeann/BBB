from a_share_agent.config import load_config
from a_share_agent.audit.event_writer import AuditEventWriter
from a_share_agent.audit.replay import ReplayEngine


def test_hash_chain(runtime_root):
    cfg = load_config(runtime_root)
    writer = AuditEventWriter(cfg)
    trade_date = "2099-01-01"
    writer.write_event(event_type="TEST_A", phase="DAILY_REVIEW", run_id="r", trace_id="t", producer={"type":"review","id":"test"}, trade_date=trade_date, payload={"a":1})
    writer.write_event(event_type="TEST_B", phase="DAILY_REVIEW", run_id="r", trace_id="t", producer={"type":"review","id":"test"}, trade_date=trade_date, payload={"b":2})
    replay = ReplayEngine(writer.audit_root, writer.index, writer.snapshot_store)
    assert replay.verify_hash_chain(trade_date)["ok"] is True
