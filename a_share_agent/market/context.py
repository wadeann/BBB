from __future__ import annotations

from statistics import mean
from typing import Any

from ..config import RuntimeConfig
from ..mcp.base import MCPInvoker
from ..utils import now_shanghai


def _bars(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("bars", "data", "kline", "klines", "items"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _close(bar: dict[str, Any]) -> float | None:
    for key in ("close", "c", "收盘"):
        if key in bar:
            try: return float(bar[key])
            except (TypeError, ValueError): return None
    return None


class MarketContextBuilder:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker):
        self.config, self.mcp = config, mcp

    def build(self) -> dict[str, Any]:
        bench_cfg = self.config.defaults.get("benchmarks", {})
        benchmarks = []
        up = down = 0
        missing = []
        for _, symbol in bench_cfg.items():
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=40)
                bs = _bars(raw)
                if not bs:
                    raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=40)
                    bs = _bars(raw)
            except Exception:
                raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=40)
                bs = _bars(raw)
            closes = [c for c in (_close(x) for x in bs) if c is not None]
            if len(closes) < 20:
                benchmarks.append({"symbol": symbol, "state": "unknown", "close": closes[-1] if closes else None, "ma20": None, "ma20_slope": None})
                missing.append(f"benchmark:{symbol}")
                continue
            ma20 = mean(closes[-20:])
            prev = mean(closes[-25:-5]) if len(closes) >= 25 else mean(closes[:20])
            slope = ma20 - prev
            state = "up" if closes[-1] > ma20 and slope > 0 else "down" if closes[-1] < ma20 and slope < 0 else "mixed"
            up += state == "up"; down += state == "down"
            benchmarks.append({"symbol": symbol, "state": state, "close": closes[-1], "ma20": ma20, "ma20_slope": slope})
        health = {"blowup_rate": 0.0, "limit_up": 0, "limit_down": 0, "total": 0, "zdt": 0}
        ladder = {"total": 0, "stocks": []}
        lanes = {"total": 0, "lanes": []}
        try:
            import httpx, os, json, threading
            from ..mcp.http import _build_auth
            mcp_cfg = self.config.runtime.get("mcp", {})
            services = mcp_cfg.get("services", {})
            intel_cfg = services.get("intel", {})
            if intel_cfg.get("enabled"):
                url_env = intel_cfg.get("url_env", "")
                url = os.environ.get(url_env) if url_env else intel_cfg.get("url", "")
                if url:
                    auth, auth_headers = _build_auth(intel_cfg)
                    headers = {"Content-Type": "application/json", **auth_headers}
                    fast = httpx.Client(timeout=5.0, auth=auth, headers=headers, verify=False)
                    def invoke_fast(tool, **kw):
                        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": tool, "arguments": kw}}
                        try:
                            resp = fast.post(url.rstrip("/"), json=payload, timeout=5.0)
                            if resp.status_code == 200:
                                return resp.json().get("result", {}).get("content", [{}])[0].get("text", "{}")
                        except:
                            pass
                        return "{}"
                    raw_h = json.loads(invoke_fast("fetch_market_health"))
                    if isinstance(raw_h, dict):
                        health.update(raw_h)
                    raw_l = json.loads(invoke_fast("get_limitup_ladder", date=None, min_streak=1))
                    if isinstance(raw_l, dict):
                        ladder.update(raw_l)
                    raw_n = json.loads(invoke_fast("get_mainline_lanes", top_n=5))
                    if isinstance(raw_n, dict):
                        lanes.update(raw_n)
                    fast.close()
        except Exception:
            pass
        blowup = float(health.get("blowup_rate", 0.0)) if isinstance(health, dict) else 0.0
        weak_blowup = float(self.config.defaults.get("market_filter", {}).get("weak_blowup_rate", .45))
        if down >= 2 or blowup >= weak_blowup:
            regime, gate, trend = "risk_off", "block_short_beta", "down"
        elif up >= 2:
            regime, gate, trend = "risk_on", "allow", "up"
        else:
            regime, gate, trend = "neutral", "reduce", "range"
        if isinstance(health, dict):
            lup = float(health.get("limit_up", 0) or 0); ldn = float(health.get("limit_down", 0) or 0)
            sentiment = "strong" if lup > max(20, ldn*3) and blowup < .30 else "weak" if ldn > lup or blowup > .40 else "neutral"
        else:
            sentiment = "unknown"
        phase = "hot" if sentiment == "strong" and regime == "risk_on" else "panic" if sentiment == "weak" and regime == "risk_off" else "warming" if sentiment == "strong" else "unknown"
        return {
            "as_of": now_shanghai().isoformat(),
            "trading_day": True,
            "session": None,
            "market_regime": regime,
            "market_trend": trend,
            "sentiment_state": sentiment,
            "sentiment_phase": phase,
            "benchmarks": benchmarks,
            "market_health": health,
            "limitup_ladder": ladder,
            "mainline_lanes": lanes,
            "market_gate": gate,
            "data_quality": {"state": "degraded" if missing else "ok", "missing": missing, "conflicts": []},
        }
