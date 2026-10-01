from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..audit.event_writer import AuditEventWriter
from ..config import RuntimeConfig
from ..core.schema_validator import SchemaRegistry
from ..models import RunContext
from ..utils import now_shanghai, stable_hash
from .base import LLMClient


STRATEGY_FAMILY = {
    "triple_golden_cross": "trend_breakout",
    "ma_convergence_breakout": "trend_breakout",
    "high_volume_breakout": "trend_breakout",
    "ma60_breakout_retest": "trend_pullback",
    "ma5_momentum_pullback": "trend_pullback",
    "single_bull_hold": "trend_pullback",
    "low_volume_support_bull": "trend_pullback",
    "long_bull_day7": "pattern_confirmation",
    "shooting_star_high": "exit_defensive",
    "volume_price_divergence": "exit_defensive",
    "ma20_break": "exit_defensive",
    "trendline_break": "exit_defensive",
    "ma_bearish_cut": "exit_defensive",
}


class DecisionAgent:
    """LLM decision layer with deterministic schema and route enforcement."""
    def __init__(self, config: RuntimeConfig, llm: LLMClient, audit: AuditEventWriter):
        self.config, self.llm, self.audit = config, llm, audit
        self.schemas = SchemaRegistry(config.project_root / "skill" / "schemas")
        self.system_prompt = (config.project_root / "skill" / "SKILL.md").read_text(encoding="utf-8")
        self.signal_schema = json.loads((config.project_root / "skill" / "schemas" / "signal_output.schema.json").read_text(encoding="utf-8"))

    def decide(self, run: RunContext, *, symbol: str, deep_dive: dict[str, Any], market_context: dict[str, Any],
               sector_context: dict[str, Any], route: dict[str, Any], portfolio: dict[str, Any] | None = None) -> dict[str, Any]:
        trace_id = str(uuid.uuid4())
        payload = {
            "task": "根据已加载 Skill 对单票作结构化信号决策；不得修改上游事实。",
            "symbol": symbol,
            "mode": run.mode,
            "phase": run.phase,
            "market_context": market_context,
            "sector_context": sector_context,
            "strategy_route": route,
            "deep_dive": deep_dive,
            "portfolio": portfolio or {},
        }
        prompt_hash = stable_hash(self.system_prompt)
        prompt_snap = self.audit.snapshot_store.put(now_shanghai().date().isoformat(), {"system": self.system_prompt, "user": payload})
        result = self.llm.complete_json(system_prompt=self.system_prompt, user_payload=payload, schema=self.signal_schema)
        self.schemas.validate("signal_output.schema.json", result)
        if result["symbol"] != symbol:
            raise ValueError(f"LLM symbol mismatch: {result['symbol']} != {symbol}")
        if result["mode"] != run.mode:
            raise ValueError(f"LLM mode mismatch: {result['mode']} != {run.mode}")
        self._enforce_route(result, route)
        response_snap = self.audit.snapshot_store.put(now_shanghai().date().isoformat(), result)
        self.audit.write_event(event_type="SIGNAL_DECISION", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"llm","id":self.config.runtime.get("model_id","external-llm")},
                               symbol=symbol, strategy_id=self._primary_strategy(result), payload=result,
                               source_refs=[prompt_snap["snapshot_ref"], response_snap["snapshot_ref"]], prompt_hash=prompt_hash)
        return result

    @staticmethod
    def _primary_strategy(result: dict[str, Any]) -> str | None:
        sigs=result.get("triggered_signals",[])
        if sigs and isinstance(sigs[0],dict): return sigs[0].get("signal")
        return None

    @staticmethod
    def _enforce_route(result: dict[str, Any], route: dict[str, Any]) -> None:
        if result.get("decision") not in {"ENTRY_CANDIDATE", "ORDER_PROPOSAL"}:
            return
        blocked=set(route.get("blocked_strategy_families",[])); allowed=set(route.get("allowed_strategy_families",[]))
        primaries=[]
        for s in result.get("triggered_signals",[]):
            if isinstance(s,dict):
                sid=s.get("signal"); fam=STRATEGY_FAMILY.get(sid)
                if fam and s.get("strength","primary") != "confirmation": primaries.append(fam)
        if any(f in blocked for f in primaries):
            raise ValueError(f"LLM entry uses route-blocked strategy family: {primaries}")
        if primaries and not any(f in allowed for f in primaries):
            raise ValueError(f"LLM entry has no route-allowed primary strategy family: {primaries}")
        threshold=75+float(route.get("candidate_threshold_delta",0))
        if float(result.get("score",0)) < threshold:
            raise ValueError(f"LLM entry score {result.get('score')} below routed threshold {threshold}")
