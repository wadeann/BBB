from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from jsonschema import validate as validate_json_schema

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

# Includes Gemini's schema-compatibility guidance explicitly in the prompt.
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
Do not modify scores, prices, market regime, sector facts, signal identifiers, or stop values. Do not estimate a win probability.
Output format: Return JSON ONLY with root key "decisions" containing an array of decisions:
{"decisions": [{"candidate_id": "<id>", "decision": "PASS"|"WATCH"|"REJECT", "confidence": 0-100, "reasons_for": ["..."], "reasons_against": ["..."], "risk_flags": ["..."]}]}
Return exactly one decision for every candidate_id in the input and JSON only."""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _ret(closes: list[float], n: int) -> float:
    if len(closes) <= n or closes[-n-1] == 0:
        return 0.0
    return closes[-1] / closes[-n-1] - 1.0


def _ma(vals: list[float], n: int) -> float:
    return sum(vals[-n:]) / n if len(vals) >= n else (sum(vals) / len(vals) if vals else 0.0)


def _atr_pct(bars: list[dict[str, Any]], n: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, l, pc = _f(bars[i].get("high")), _f(bars[i].get("low")), _f(bars[i-1].get("close"))
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    atr = _ma(trs, min(n, len(trs)))
    close = _f(bars[-1].get("close"))
    return atr / close if close > 0 else 0.0


def _compact_features(bars: list[dict[str, Any]], stop: Any) -> dict[str, Any]:
    rows = bars[-60:]
    closes = [_f(x.get("close")) for x in rows]
    vols = [_f(x.get("volume")) for x in rows]
    last = rows[-1] if rows else {}
    close = closes[-1] if closes else 0.0
    ma5, ma20 = _ma(closes, 5), _ma(closes, 20)
    prev_ma20 = _ma(closes[:-5], 20) if len(closes) >= 25 else ma20
    hi20 = max((_f(x.get("high")) for x in rows[-20:]), default=close)
    lo20 = min((_f(x.get("low")) for x in rows[-20:]), default=close)
    v20 = _ma(vols, 20)
    o, h, l = _f(last.get("open")), _f(last.get("high")), _f(last.get("low"))
    rng = max(h-l, 1e-12)
    body_top = max(o, close)
    return {
        "close": close,
        "daily_return": (close / o - 1.0) if o > 0 else 0.0,
        "return_5d": _ret(closes, 5),
        "return_10d": _ret(closes, 10),
        "return_20d": _ret(closes, 20),
        "return_40d": _ret(closes, 40),
        "distance_ma5": (close / ma5 - 1.0) if ma5 > 0 else 0.0,
        "distance_ma20": (close / ma20 - 1.0) if ma20 > 0 else 0.0,
        "ma20_slope_5d": (ma20 / prev_ma20 - 1.0) if prev_ma20 > 0 else 0.0,
        "volume_ratio_20d": (vols[-1] / v20) if vols and v20 > 0 else 0.0,
        "volume_ratio_5v20": (_ma(vols, 5) / v20) if v20 > 0 else 0.0,
        "distance_20d_high": (close / hi20 - 1.0) if hi20 > 0 else 0.0,
        "rebound_from_20d_low": (close / lo20 - 1.0) if lo20 > 0 else 0.0,
        "atr14_pct": _atr_pct(rows, 14),
        "stop_distance_pct": ((close - _f(stop)) / close) if close > 0 and _f(stop) > 0 else 0.0,
        "upper_shadow_ratio": max(0.0, h-body_top) / rng,
        "lower_shadow_ratio": max(0.0, min(o,close)-l) / rng,
    }


class HistoricalLLMFilter:
    """Point-in-time LLM gate with deterministic cache and explicit ERROR semantics.

    Transport/schema failures are never converted into model REJECT decisions. This
    prevents infrastructure outages from masquerading as successful risk filtering.
    """

    def __init__(self, root: Path, llm: LLMClient, *, model_id: str = "external-llm", use_cache: bool = True,
                 anonymize_symbol: bool = True, batch_size: int = 10, payload_mode: str = "compact_features"):
        self.root = Path(root)
        self.llm = llm
        self.model_id = model_id
        self.use_cache = use_cache
        self.anonymize_symbol = anonymize_symbol
        self.batch_size = max(1, int(batch_size))
        self.payload_mode = payload_mode
        self.cache_root = self.root / "data" / "backtest" / "llm_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0
        self.candidates_reviewed = 0
        self.successful_candidates = 0
        self.error_candidates = 0
        self.latency_seconds_total = 0.0
        self.decision_counts: dict[str, int] = {}

    def _path(self, key: str) -> Path:
        return self.cache_root / f"{key}.json"

    def _candidate_payload(self, as_of: str, symbol: str, candidate: dict[str, Any], recent_bars: list[dict[str, Any]]) -> dict[str, Any]:
        candidate_id = stable_hash({"as_of": as_of, "symbol": symbol, "strategy": candidate.get("primary", {}).get("signal")})[:16]
        display_symbol = ("SEC_" + stable_hash(symbol)[:10]) if self.anonymize_symbol else symbol
        base = {
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
        if self.payload_mode == "compact_features":
            base["historical_features"] = _compact_features(recent_bars, candidate.get("stop"))
        else:
            base["recent_daily_bars"] = recent_bars[-40:]
        return base

    @staticmethod
    def _validated_decisions(response: Any, expected_ids: set[str]) -> list[dict[str, Any]]:
        decisions = response.get("decisions") if isinstance(response, dict) else None
        validate_json_schema(instance={"decisions": decisions}, schema=BATCH_SCHEMA)
        if not isinstance(decisions, list):
            raise ValueError("LLM batch response missing decisions")
        ids = [str(d.get("candidate_id")) for d in decisions if isinstance(d, dict)]
        if len(ids) != len(set(ids)) or set(ids) != expected_ids:
            raise ValueError("LLM batch response candidate_id set mismatch")
        return decisions

    def _call_chunk(self, *, as_of: str, rows: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
        payload_candidates = [self._candidate_payload(as_of, sym, cand, bars) for sym, cand, bars in rows]
        id_to_symbol = {x["candidate_id"]: rows[i][0] for i, x in enumerate(payload_candidates)}
        payload = {"task": "historical_candidate_gate_batch", "as_of": as_of, "candidates": payload_candidates}
        key = stable_hash({
            "prompt": SYSTEM_PROMPT, "schema": BATCH_SCHEMA, "model": self.model_id,
            "payload_mode": self.payload_mode, "actual_symbols": [x[0] for x in rows], "payload": payload,
        })
        path = self._path(key)
        if self.use_cache and path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                decisions = self._validated_decisions(cached, set(id_to_symbol))
                self.cache_hits += 1
                out = {id_to_symbol[str(d["candidate_id"])]: {**d, "cache_key": key, "cached": True} for d in decisions}
                self.candidates_reviewed += len(rows)
                self.successful_candidates += len(rows)
                for d in decisions:
                    dec = str(d.get("decision"))
                    self.decision_counts[dec] = self.decision_counts.get(dec, 0) + 1
                return out
            except Exception:
                # Old/incompatible cache is ignored and replaced by a fresh valid call.
                pass

        self.calls += 1
        self.candidates_reviewed += len(rows)
        started = time.monotonic()
        try:
            response = self.llm.complete_json(system_prompt=SYSTEM_PROMPT, user_payload=payload, schema=BATCH_SCHEMA)
            decisions = self._validated_decisions(response, set(id_to_symbol))
            elapsed = time.monotonic() - started
            self.latency_seconds_total += elapsed
            record = {"decisions": decisions, "model_id": self.model_id, "cache_key": key, "payload_mode": self.payload_mode}
            if self.use_cache:
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            self.successful_candidates += len(rows)
            for d in decisions:
                dec = str(d.get("decision"))
                self.decision_counts[dec] = self.decision_counts.get(dec, 0) + 1
            return {id_to_symbol[str(d["candidate_id"])]: {**d, "cache_key": key, "cached": False} for d in decisions}
        except Exception as exc:
            self.latency_seconds_total += time.monotonic() - started
            self.failures += 1
            self.error_candidates += len(rows)
            self.decision_counts["ERROR"] = self.decision_counts.get("ERROR", 0) + len(rows)
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

    def decide_batch(self, *, as_of: str, candidates: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(candidates), self.batch_size):
            out.update(self._call_chunk(as_of=as_of, rows=candidates[i:i + self.batch_size]))
        return out

    def decide(self, *, as_of: str, symbol: str, candidate: dict[str, Any], recent_bars: list[dict[str, Any]]) -> dict[str, Any]:
        return self.decide_batch(as_of=as_of, candidates=[(symbol, candidate, recent_bars)])[symbol]

    def stats(self) -> dict[str, Any]:
        call_failure_rate = self.failures / self.calls if self.calls else 0.0
        candidate_error_rate = self.error_candidates / self.candidates_reviewed if self.candidates_reviewed else 0.0
        return {
            "api_calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "call_failure_rate": call_failure_rate,
            "candidates_reviewed": self.candidates_reviewed,
            "successful_candidates": self.successful_candidates,
            "error_candidates": self.error_candidates,
            "candidate_error_rate": candidate_error_rate,
            "decision_counts": dict(self.decision_counts),
            "latency_seconds_total": self.latency_seconds_total,
            "avg_api_call_seconds": self.latency_seconds_total / self.calls if self.calls else 0.0,
            "model_id": self.model_id,
            "symbol_anonymized": self.anonymize_symbol,
            "batch_size": self.batch_size,
            "payload_mode": self.payload_mode,
            "historical_knowledge_contamination_fully_eliminated": False,
        }
