from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormattedNotification:
    title: str
    severity: str
    text: str
    fields: list[tuple[str, str]]
    event_type: str
    symbol: str | None = None


class NotificationFormatter:
    """Turn immutable audit events into concise operator-facing messages."""

    TRADE_EVENTS = {"ORDER_PROPOSAL", "ORDER_REQUEST", "EXECUTION_RECEIPT"}
    ALERT_EVENTS = {"SIGNAL_DECISION", "RISK_RESULT", "AUDIT_GAP"}

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.min_signal_score = float(self.config.get("min_signal_score", 75))
        self.signal_decisions = set(self.config.get("signal_decisions", ["ENTRY_CANDIDATE", "EXIT_CANDIDATE", "ORDER_PROPOSAL"]))
        self.notify_risk_pass = bool(self.config.get("notify_risk_pass", False))

    @staticmethod
    def payload(event: dict[str, Any]) -> dict[str, Any]:
        raw = event.get("payload")
        if isinstance(raw, dict):
            return raw
        raw_json = event.get("payload_json")
        if isinstance(raw_json, str) and raw_json:
            try:
                val = json.loads(raw_json)
                return val if isinstance(val, dict) else {"value": val}
            except Exception:
                return {}
        return {}

    def should_notify(self, event: dict[str, Any]) -> bool:
        et = str(event.get("event_type") or "")
        payload = self.payload(event)
        if et in self.TRADE_EVENTS or et == "AUDIT_GAP":
            return True
        if et == "SIGNAL_DECISION":
            decision = str(payload.get("decision", "")).upper()
            try:
                score = float(payload.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0
            return decision in self.signal_decisions and (decision.startswith("EXIT") or score >= self.min_signal_score)
        if et == "RISK_RESULT":
            status = str(payload.get("status") or event.get("status") or "").upper()
            return self.notify_risk_pass or status not in {"PASS", "OK"}
        return False

    def format(self, event: dict[str, Any]) -> FormattedNotification:
        et = str(event.get("event_type") or "UNKNOWN")
        p = self.payload(event)
        symbol = event.get("symbol") or p.get("symbol")
        strategy = event.get("strategy_id") or p.get("strategy_id")
        phase = event.get("phase") or "--"
        time_str = str(event.get("event_time") or "")
        hhmmss = time_str.split("T", 1)[-1][:8] if "T" in time_str else time_str

        if et == "SIGNAL_DECISION":
            decision = str(p.get("decision", "SIGNAL"))
            score = p.get("score", "--")
            severity = "warning" if decision == "ENTRY_CANDIDATE" else "danger" if "EXIT" in decision else "info"
            title = f"信号预警 · {decision}"
            fields = [("股票", str(symbol or "--")), ("评分", str(score)), ("策略", str(strategy or "--")), ("阶段", str(phase)), ("时间", hhmmss)]
            reasons = p.get("reasons_for") or p.get("reasons") or []
            against = p.get("reasons_against") or []
            text = self._join_reason(reasons, "触发")
            if against:
                text += ("\n" if text else "") + self._join_reason(against, "反证")
            return FormattedNotification(title, severity, text or "策略信号已生成。", fields, et, symbol)

        if et in {"ORDER_PROPOSAL", "ORDER_REQUEST"}:
            direction = str(p.get("direction", "--")).upper()
            severity = "danger" if direction == "SELL" else "warning"
            title = "卖出交易提案" if direction == "SELL" else "买入交易提案"
            fields = [
                ("股票", str(symbol or p.get("symbol") or "--")),
                ("方向", direction),
                ("数量", str(p.get("quantity", p.get("max_quantity", "--")))),
                ("价格", str(p.get("price", p.get("limit_price", "--")))),
                ("策略", str(strategy or "--")),
                ("阶段", str(phase)),
            ]
            return FormattedNotification(title, severity, str(p.get("reason") or "交易请求已生成。"), fields, et, symbol)

        if et == "EXECUTION_RECEIPT":
            direction = str(p.get("direction", "--")).upper()
            status = str(p.get("status", event.get("status", "--"))).upper()
            severity = "success" if status in {"FILLED", "SUCCESS", "SUBMITTED", "ACCEPTED"} else "danger"
            title = f"成交/委托回执 · {status}"
            fields = [
                ("股票", str(symbol or p.get("symbol") or "--")),
                ("方向", direction),
                ("数量", str(p.get("quantity", "--"))),
                ("价格", str(p.get("price", p.get("avg_price", "--")))),
                ("订单", str(p.get("order_id", "--"))),
                ("时间", hhmmss),
            ]
            return FormattedNotification(title, severity, "执行通道已返回结果。", fields, et, symbol)

        if et == "RISK_RESULT":
            status = str(p.get("status") or event.get("status") or "UNKNOWN").upper()
            title = f"风控结果 · {status}"
            severity = "success" if status == "PASS" else "danger"
            local = p.get("local") if isinstance(p.get("local"), dict) else {}
            reason_codes = local.get("reason_codes") or p.get("reason_codes") or []
            fields = [("股票", str(symbol or "--")), ("结果", status), ("策略", str(strategy or "--")), ("阶段", str(phase))]
            text = "原因：" + ", ".join(map(str, reason_codes)) if reason_codes else "风控校验完成。"
            return FormattedNotification(title, severity, text, fields, et, symbol)

        if et == "AUDIT_GAP":
            return FormattedNotification("系统审计告警", "danger", "检测到审计链异常或缺口，请暂停自主执行并检查。", [("阶段", str(phase)), ("时间", hhmmss)], et, symbol)

        return FormattedNotification(et, "info", "系统事件。", [("阶段", str(phase)), ("时间", hhmmss)], et, symbol)

    @staticmethod
    def _join_reason(value: Any, label: str) -> str:
        if isinstance(value, list):
            parts = [str(x) for x in value[:4] if x]
            return f"{label}：" + "；".join(parts) if parts else ""
        if value:
            return f"{label}：{value}"
        return ""
