from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from a_share_agent.audit.event_writer import AuditEventWriter
from a_share_agent.config import load_config
from a_share_agent.event_stream import AuditSSEStream
from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.runtime import AgentRuntime
from a_share_agent.worker import BackgroundWorker


class NonTradingFake(FakeMCPInvoker):
    def invoke(self, tool_name: str, **kwargs):
        if tool_name == "mcp_intel_is_trading_day":
            return {"trading_day": False}
        return super().invoke(tool_name, **kwargs)


def test_worker_phase_slot_is_persistent_and_idempotent(runtime_root):
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    worker = BackgroundWorker(rt, owner_id="pytest-worker")
    dt = datetime(2026, 10, 8, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = worker.tick(dt)
    second = worker.tick(dt)
    assert first["state"] == "SUCCESS"
    assert first["phase"] == "ENTRY_WINDOW_AM"
    assert second["state"] == "ALREADY_RAN"


def test_worker_does_not_schedule_non_trading_day(runtime_root):
    rt = AgentRuntime(runtime_root, NonTradingFake())
    worker = BackgroundWorker(rt, owner_id="pytest-holiday")
    dt = datetime(2026, 10, 8, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    out = worker.tick(dt)
    assert out["state"] == "NON_TRADING_DAY"
    assert not rt.worker_state.phase_history("2026-10-08")


def test_audit_sse_stream_reads_cross_process_index(runtime_root):
    cfg = load_config(runtime_root)
    writer = AuditEventWriter(cfg)
    writer.write_event(
        event_type="SSE_TEST",
        phase="DAILY_REVIEW",
        run_id="r1",
        trace_id="t1",
        producer={"type": "test", "id": "pytest"},
        trade_date="2099-01-02",
        payload={"ok": True},
    )
    stream = AuditSSEStream(writer.index, poll_seconds=0.01, heartbeat_seconds=1)

    async def read_one():
        agen = stream.iterate(after_seq=0)
        try:
            return await anext(agen)
        finally:
            await agen.aclose()

    chunk = asyncio.run(read_one())
    assert "event: audit" in chunk
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line[6:])
    assert payload["event_type"] == "SSE_TEST"
