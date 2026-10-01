from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(tz=SH_TZ)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SH_TZ)
    return dt


def as_trade_date(dt: datetime | None = None) -> str:
    dt = dt or now_shanghai()
    return dt.astimezone(SH_TZ).date().isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def redact(value: Any, never_log: set[str] | None = None, hash_fields: set[str] | None = None) -> Any:
    never_log = {x.lower() for x in (never_log or set())}
    hash_fields = {x.lower() for x in (hash_fields or set())}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lk = str(key).lower()
            if lk in never_log or any(secret in lk for secret in ("password", "api_key", "access_token", "refresh_token", "broker_secret", "secret", "authorization", "webhook_url", "webhook")):
                out[key] = "<REDACTED>"
            elif lk in hash_fields:
                out[key] = f"sha256:{sha256_hex(str(item))}"
            else:
                out[key] = redact(item, never_log, hash_fields)
        return out
    if isinstance(value, list):
        return [redact(x, never_log, hash_fields) for x in value]
    return json_safe(value)


def extract_symbols(value: Any) -> list[str]:
    """Best-effort extraction from heterogeneous screener payloads."""
    found: list[str] = []
    pat = re.compile(r"\b(?:\d{6}\.(?:SH|SZ|BJ)|(?:SH|SZ|BJ)\d{6}|\d{6})\b", re.I)

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if str(k).lower() in {"symbol", "code", "stock_code", "证券代码", "股票代码"} and isinstance(x, (str, int)):
                    s = normalize_symbol(str(x))
                    if s:
                        found.append(s)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for m in pat.findall(v):
                s = normalize_symbol(m)
                if s:
                    found.append(s)
    walk(value)
    return list(dict.fromkeys(found))


def normalize_symbol(raw: str) -> str | None:
    s = raw.strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s):
        return s
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", s):
        return f"{s[2:]}.{s[:2]}"
    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("6", "9")):
            return f"{s}.SH"
        if s.startswith(("0", "3")):
            return f"{s}.SZ"
        if s.startswith(("4", "8")):
            return f"{s}.BJ"
    return None
