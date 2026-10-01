from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .formatter import FormattedNotification


class NotificationError(RuntimeError):
    pass


def _env_or_value(cfg: dict[str, Any], value_key: str, env_key: str, *, required: bool = False) -> str | None:
    value = cfg.get(value_key)
    if value is not None and str(value).strip():
        return str(value)
    env_name = cfg.get(env_key)
    if env_name:
        value = os.environ.get(str(env_name))
        if value:
            return value
        if required:
            raise NotificationError(f"missing required environment variable: {env_name}")
    if required:
        raise NotificationError(f"missing required notification setting: {value_key} or {env_key}")
    return None


@dataclass
class SendReceipt:
    ok: bool
    channel: str
    status_code: int | None = None
    provider_code: Any = None
    provider_message: str | None = None


class FeishuWebhookChannel:
    name = "feishu"

    def __init__(self, cfg: dict[str, Any], *, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.timeout = float(cfg.get("timeout_seconds", 10))
        self.format = str(cfg.get("format", "interactive"))
        self._transport = transport

    @staticmethod
    def _sign(timestamp: int, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def configured(self) -> bool:
        try:
            return bool(_env_or_value(self.cfg, "webhook_url", "webhook_url_env", required=False))
        except Exception:
            return False

    def _payload(self, msg: FormattedNotification) -> dict[str, Any]:
        timestamp = int(time.time())
        payload: dict[str, Any]
        if self.format == "text":
            body = [msg.title]
            body.extend(f"{k}: {v}" for k, v in msg.fields)
            if msg.text:
                body.append(msg.text)
            payload = {"msg_type": "text", "content": {"text": "\n".join(body)}}
        else:
            template = {"success": "green", "warning": "orange", "danger": "red", "info": "blue"}.get(msg.severity, "blue")
            field_md = "\n".join(f"**{k}**：{v}" for k, v in msg.fields)
            body = field_md + (f"\n\n{msg.text}" if msg.text else "")
            payload = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {"template": template, "title": {"tag": "plain_text", "content": msg.title}},
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body[:3800]}}],
                },
            }
        secret = _env_or_value(self.cfg, "secret", "secret_env", required=False)
        if secret:
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp, secret)
        return payload

    def send(self, msg: FormattedNotification) -> SendReceipt:
        url = _env_or_value(self.cfg, "webhook_url", "webhook_url_env", required=True)
        payload = self._payload(msg)
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport, follow_redirects=True) as client:
                resp = client.post(url or "", json=payload)
        except httpx.HTTPError as exc:
            raise NotificationError(f"feishu transport failure: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise NotificationError(f"feishu HTTP {resp.status_code}")
        try:
            result = resp.json()
        except ValueError as exc:
            raise NotificationError("feishu returned non-JSON response") from exc
        code = result.get("code", result.get("StatusCode", 0)) if isinstance(result, dict) else 0
        msg_text = result.get("msg", result.get("StatusMessage")) if isinstance(result, dict) else None
        if code not in (0, "0", None):
            raise NotificationError(f"feishu provider error code={code}: {msg_text or 'unknown error'}")
        return SendReceipt(True, self.name, resp.status_code, code, str(msg_text) if msg_text is not None else None)

    def probe(self) -> dict[str, Any]:
        try:
            receipt = self.send(FormattedNotification(
                title="A股 Agent 通知测试",
                severity="info",
                text="通知通道配置成功。此消息不代表任何交易信号。",
                fields=[("类型", "部署自检"), ("通道", "飞书 Webhook")],
                event_type="NOTIFICATION_PROBE",
            ))
            return {"ok": True, "channel": self.name, "status_code": receipt.status_code}
        except Exception as exc:
            return {"ok": False, "channel": self.name, "error": str(exc)}
