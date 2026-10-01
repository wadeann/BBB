from __future__ import annotations

import json

import httpx

from a_share_agent.audit.event_writer import AuditEventWriter
from a_share_agent.config import load_config
from a_share_agent.notifications.channels import FeishuWebhookChannel
from a_share_agent.notifications.dispatcher import NotificationDispatcher
from a_share_agent.notifications.formatter import FormattedNotification, NotificationFormatter


def test_notification_formatter_filters_signals_and_risk():
    fmt = NotificationFormatter({"min_signal_score": 75, "notify_risk_pass": False})
    watch = {"event_type": "SIGNAL_DECISION", "payload_json": json.dumps({"decision": "WATCH", "score": 90})}
    entry = {"event_type": "SIGNAL_DECISION", "symbol": "600000.SH", "phase": "ENTRY_WINDOW_AM", "payload_json": json.dumps({"decision": "ENTRY_CANDIDATE", "score": 84, "reasons_for": ["MA60回踩确认"]})}
    risk_pass = {"event_type": "RISK_RESULT", "payload_json": json.dumps({"status": "PASS"})}
    risk_reject = {"event_type": "RISK_RESULT", "payload_json": json.dumps({"status": "REJECT", "local": {"reason_codes": ["MAX_POSITION"]}})}
    assert fmt.should_notify(watch) is False
    assert fmt.should_notify(entry) is True
    assert fmt.format(entry).title.startswith("信号预警")
    assert fmt.should_notify(risk_pass) is False
    assert fmt.should_notify(risk_reject) is True


def test_feishu_channel_interactive_and_signature(monkeypatch):
    monkeypatch.setenv("TEST_FEISHU_URL", "https://feishu.invalid/hook")
    monkeypatch.setenv("TEST_FEISHU_SECRET", "sign-secret")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    ch = FeishuWebhookChannel({
        "webhook_url_env": "TEST_FEISHU_URL",
        "secret_env": "TEST_FEISHU_SECRET",
        "format": "interactive",
    }, transport=httpx.MockTransport(handler))
    receipt = ch.send(FormattedNotification(
        title="买入交易提案", severity="warning", text="测试",
        fields=[("股票", "600000.SH"), ("数量", "1000")], event_type="ORDER_REQUEST", symbol="600000.SH",
    ))
    assert receipt.ok is True
    assert seen["msg_type"] == "interactive"
    assert seen.get("timestamp")
    assert seen.get("sign")
    assert "600000.SH" in seen["card"]["elements"][0]["text"]["content"]


def test_dispatcher_persistent_dedup(runtime_root):
    cfg = load_config(runtime_root)
    writer = AuditEventWriter(cfg)

    class Sink:
        def __init__(self): self.messages = []
        def send(self, msg): self.messages.append(msg)

    sink = Sink()
    dispatcher = NotificationDispatcher(cfg, writer.index, channels={"feishu": sink})
    writer.write_event(
        event_type="ORDER_REQUEST", phase="ENTRY_WINDOW_AM", run_id="r-notify", trace_id="t-notify",
        producer={"type": "test", "id": "pytest"}, trade_date="2099-01-03", symbol="600000.SH",
        strategy_id="ma60_breakout_retest",
        payload={"symbol":"600000.SH","direction":"BUY","quantity":1000,"price":10.2,"reason":"test"},
    )
    first = dispatcher.drain_once()
    second = dispatcher.drain_once()
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert len(sink.messages) == 1
    recent = dispatcher.recent(10)
    assert recent[0]["status"] == "SENT"
