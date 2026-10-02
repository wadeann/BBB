from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from jsonschema import ValidationError, validate as validate_json_schema

from ..llm.base import LLMClient
from ..utils import stable_hash


DECISION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["PASS", "WATCH", "REJECT"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "reasons_for": {"type": "array", "items": {"type": "string"}},
        "reasons_against": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidate_id", "decision", "confidence", "reasons_for", "reasons_against", "risk_flags"],
    "additionalProperties": False,
}

BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": DECISION_ITEM_SCHEMA}},
    "required": ["decisions"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the historical candidate-filter layer of an A-share trading research system.
The deterministic engine has already identified and scored each candidate. Your job is NOT to invent trades; independently challenge each candidate and output PASS, WATCH, or REJECT.

Use ONLY point-in-time data inside this request. Never use future prices, future news, future financials, later company outcomes, or external tools/web/MCP. Treat every security ID as anonymous.

Review each candidate for:
1) whether the supplied primary signal evidence is internally coherent;
2) trend and volume/price confirmation versus overextension or weak follow-through;
3) conflicts with supplied exit/defensive signals;
4) consistency with the supplied market regime, sector strength/lifecycle, and route;
5) stop distance and obvious technical fragility;
6) at least one counterargument / reasons_against.

For every candidate return ALL fields exactly: candidate_id, decision, confidence, reasons_for, reasons_against, risk_flags.
Keep reasons concise. Do not output chain-of-thought or hidden reasoning; only short evidence statements.
Do not modify scores, prices, market regime, sector facts, signal identifiers, or stop values. Do not estimate a win probability. Return exactly one decision for every candidate_id in the input and JSON only."""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _ret(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or not closes[-n-1]:
        return None
    return closes[-1] / closes[-n-1] - 1


def _sma(vals: list[float], n: int) -> float | None:
    return mean(vals[-n:]) if len(vals) >= n else None


def _compact_features(recent_bars: list[dict[str, Any]], stop: float | None) -> dict[str, Any]:
    bars = recent_bars[-60:]
    closes = [_f(x.get("close")) for x in bars]
    highs = [_f(x.get("high")) for x in bars]
    lows = [_f(x.get("low")) for x in bars]
    vols = [_f(x.get("volume")) for x in bars]
    if not closes:
        return {"bars": 0}
    close = closes[-1]
    ma5, ma20 = _sma(closes, 5), _sma(closes, 20)
    prev_ma20 = mean(closes[-25:-5]) if len(closes) >= 25 else ma20
    vol5, vol20 = _sma(vols, 5), _sma(vols, 20)
    high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    low20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    last = bars[-1]
    open_ = _f(last.get("open"))
    atr_proxy = mean([h-l for h, l in zip(highs[-14:], lows[-14:])]) if len(highs) >= 14 else None
    return {
        "bars": len(bars),
        "close": close,
        "last_day_return": (close/open_-1) if open_ else None,
        "ret_5d": _ret(closes, 5),
        "ret_10d": _ret(closes, 10),
        "ret_20d": _ret(closes, 20),
        "ret_40d": _ret(closes, 40),
        "distance_ma5": (close/ma5-1) if ma5 else None,
        "distance_ma20": (close/ma20-1) if ma20 else None,
        "ma20_slope_proxy": (ma20/prev_ma20-1) if ma20 and prev_ma20 else None,
        "volume_ratio_5_20": (vol5/vol20) if vol5 and vol20 else None,
        "last_volume_vs_20d": (vols[-1]/vol20) if vol20 else None,
        "distance_20d_high": (close/high20-1) if high20 else None,
        "rebound_from_20d_low": (close/low20-1) if low20 else None,
        "atr_proxy_pct": (atr_proxy/close) if atr_proxy and close else None,
        "stop_distance_pct": ((close-float(stop))/close) if stop and close else None,
    }


class HistoricalLLMFilter:
    """Point-in-time LLM candidate gate for research.

    Research semantics intentionally differ from live fail-closed semantics: transport or
    schema failures are returned as decision=ERROR and counted. The backtest may exclude
    those candidates for safety, but the research report MUST be invalidated rather than
    interpreting transport failures as intelligent REJECT decisions.
    """

    def __init__(self, root: Path, llm: LLMClient, *, model_id: str = "external-llm", use_cache: bool = True,
                 anonymize_symbol: bool = True, batch_size: int = 1, payload_mode: str = "compact_features",
                 max_bars: int = 20):
        self.root = Path(root)
        self.llm = llm
        self.model_id = model_id
        self.use_cache = use_cache
        self.anonymize_symbol = anonymize_symbol
        self.batch_size = max(1, int(batch_size))
        self.payload_mode = str(payload_mode or "compact_features")
        self.max_bars = max(5, int(max_bars))
        self.cache_root = self.root / "data" / "backtest" / "llm_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0
        self.error_candidates = 0
        self.candidates_reviewed = 0
        self.latency_seconds = 0.0
        self.decision_counts: Counter[str] = Counter()

    def _path(self, key: str) -> Path:
        return self.cache_root / f"{key}.json"

    def _candidate_payload(self, as_of: str, symbol: str, candidate: dict[str, Any], recent_bars: list[dict[str, Any]]) -> dict[str, Any]:
        candidate_id = stable_hash({"as_of": as_of, "symbol": symbol, "strategy": candidate.get("primary", {}).get("signal")})[:16]
        display_symbol = ("SEC_" + stable_hash(symbol)[:10]) if self.anonymize_symbol else symbol
        payload = {
            "candidate_id": candidate_id,
            "security_id": display_symbol,
            "deterministic_candidate": {
                "strategy": candidate.get("primary"),
                "score": candidate.get("score"),
                "score_breakdown": candidate.get("breakdown"),
                "route": candidate.get("route"),
                "market": candidate.get("market"),
                "sector": candidate.get("sector"),
                "hits": candidate.get("hits"),
                "stop": candidate.get("stop"),
            },
        }
        if self.payload_mode == "bars":
            payload["recent_daily_bars"] = recent_bars[-self.max_bars:]
        else:
            payload["historical_features"] = _compact_features(recent_bars, candidate.get("stop"))
        return payload

    @staticmethod
    def _validate_decisions(decisions: Any, id_to_symbol: dict[str, str]) -> list[dict[str, Any]]:
        record = {"decisions": decisions}
        try:
            validate_json_schema(instance=record, schema=BATCH_SCHEMA)
        except ValidationError as exc:
            raise ValueError(f"LLM decision schema invalid: {exc.message}") from exc
        by_id = {str(d.get("candidate_id")): d for d in decisions if isinstance(d, dict)}
        if set(by_id) != set(id_to_symbol):
            raise ValueError("LLM batch response candidate_id set mismatch")
        return list(decisions)

    def _call_chunk(self, *, as_of: str, rows: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
        payload_candidates = [self._candidate_payload(as_of, sym, cand, bars) for sym, cand, bars in rows]
        id_to_symbol = {x["candidate_id"]: rows[i][0] for i, x in enumerate(payload_candidates)}
        payload = {"task": "historical_candidate_gate_batch", "as_of": as_of, "candidates": payload_candidates}
        key = stable_hash({
            "prompt": SYSTEM_PROMPT, "schema": BATCH_SCHEMA, "model": self.model_id,
            "actual_symbols": [x[0] for x in rows], "payload": payload,
        })
        path = self._path(key)
        if self.use_cache and path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                decisions = cached.get("decisions") if isinstance(cached, dict) else None
                decisions = self._validate_decisions(decisions, id_to_symbol)
                self.cache_hits += 1
                self.candidates_reviewed += len(rows)
                out = {}
                for d in decisions:
                    self.decision_counts[str(d.get("decision"))] += 1
                    out[id_to_symbol[d["candidate_id"]]] = {**d, "cache_key": key, "cached": True}
                return out
            except Exception:
                # Old/non-conforming cache must never silently influence a new experiment.
                pass

        self.calls += 1
        self.candidates_reviewed += len(rows)
        started = time.monotonic()
        try:
            response = self.llm.complete_json(system_prompt=SYSTEM_PROMPT, user_payload=payload, schema=BATCH_SCHEMA)
            decisions = response.get("decisions") if isinstance(response, dict) else None
            decisions = self._validate_decisions(decisions, id_to_symbol)
            record = {"decisions": decisions, "model_id": self.model_id, "cache_key": key, "payload_mode": self.payload_mode}
            if self.use_cache:
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            out = {}
            for d in decisions:
                self.decision_counts[str(d.get("decision"))] += 1
                out[id_to_symbol[d["candidate_id"]]] = {**d, "cache_key": key, "cached": False}
            return out
        except Exception as exc:
            self.failures += 1
            self.error_candidates += len(rows)
            self.decision_counts["ERROR"] += len(rows)
            return {
                sym: {
                    "candidate_id": payload_candidates[i]["candidate_id"],
                    "decision": "ERROR", "confidence": 0,
                    "reasons_for": [], "reasons_against": ["LLM_FILTER_ERROR"],
                    "risk_flags": [type(exc).__name__], "cache_key": key, "cached": False,
                    "error": str(exc),
                }
                for i, (sym, _cand, _bars) in enumerate(rows)
            }
        finally:
            self.latency_seconds += max(0.0, time.monotonic() - started)

    def decide_batch(self, *, as_of: str, candidates: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(candidates), self.batch_size):
            out.update(self._call_chunk(as_of=as_of, rows=candidates[i:i + self.batch_size]))
        return out

    def decide(self, *, as_of: str, symbol: str, candidate: dict[str, Any], recent_bars: list[dict[str, Any]]) -> dict[str, Any]:
        return self.decide_batch(as_of=as_of, candidates=[(symbol, candidate, recent_bars)])[symbol]

    def stats(self) -> dict[str, Any]:
        completed = max(0, self.candidates_reviewed - self.error_candidates)
        return {
            "api_calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "call_failure_rate": (self.failures / self.calls) if self.calls else 0.0,
            "candidates_reviewed": self.candidates_reviewed,
            "successful_candidates": completed,
            "error_candidates": self.error_candidates,
            "candidate_error_rate": (self.error_candidates / self.candidates_reviewed) if self.candidates_reviewed else 0.0,
            "decision_counts": dict(self.decision_counts),
            "latency_seconds_total": self.latency_seconds,
            "avg_api_call_seconds": (self.latency_seconds / self.calls) if self.calls else 0.0,
            "model_id": self.model_id,
            "symbol_anonymized": self.anonymize_symbol,
            "batch_size": self.batch_size,
            "payload_mode": self.payload_mode,
            "max_bars": self.max_bars,
            "historical_knowledge_contamination_fully_eliminated": False,
        }
