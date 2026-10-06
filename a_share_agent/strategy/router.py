from __future__ import annotations

from typing import Any
import uuid

from ..utils import now_shanghai


class StrategyRouter:
    def __init__(self, config: dict[str, Any], *, policy_loader=None, audit=None):
        self.cfg = config
        self.routes = {r["id"]: r for r in config.get("routes", [])}
        self.policy_loader = policy_loader
        self.audit = audit

    @staticmethod
    def _matches(route: dict[str, Any], ctx: dict[str, Any]) -> bool:
        for k, v in route.get("when", {}).items():
            if ctx.get(k) != v:
                return False
        return True

    def route(self, *, market_context: dict[str, Any], sector_context: dict[str, Any], signal=None, as_of=None, audit_context=None) -> dict[str, Any]:
        ctx = {
            "market_regime": market_context.get("market_regime", "unknown"),
            "market_trend": market_context.get("market_trend", "unknown"),
            "sentiment_phase": market_context.get("sentiment_phase", "unknown"),
            "sector_strength": sector_context.get("sector_strength", "unknown"),
            "sector_lifecycle": sector_context.get("sector_lifecycle", "unknown"),
        }
        if ctx["market_regime"] == "unknown":
            chosen = {"id": "UNKNOWN_MARKET", "allow": ["exit_defensive"], "block": ["trend_breakout", "trend_pullback", "rebound_reversal", "pattern_confirmation"], "conditional": [], "position_multiplier": 0.0, "candidate_threshold_delta": 999, "max_new_positions_override": 0}
        elif ctx["sector_strength"] == "unknown":
            chosen = {"id": "UNKNOWN_SECTOR", "allow": ["exit_defensive"], "block": ["trend_breakout", "rebound_reversal"], "conditional": ["trend_pullback"], "position_multiplier": 0.25, "candidate_threshold_delta": 12, "max_new_positions_override": 0}
        else:
            order = self.cfg.get("precedence", [])
            chosen = None
            for rid in order:
                if rid == "exact_market_sector_match":
                    for r in self.cfg.get("routes", []):
                        if r["id"] in order:
                            continue
                        if self._matches(r, ctx):
                            chosen = r; break
                else:
                    r = self.routes.get(rid)
                    if r and self._matches(r, ctx):
                        chosen = r
                if chosen: break
            if not chosen:
                chosen = {"id": "FALLBACK_CONSERVATIVE", "allow": ["exit_defensive"], "conditional": ["trend_pullback"], "block": ["trend_breakout", "rebound_reversal"], "position_multiplier": .35, "candidate_threshold_delta": 10, "max_new_positions_override": 0}
        result = {
            "as_of": now_shanghai().isoformat(),
            "route_id": chosen["id"],
            **ctx,
            "allowed_strategy_families": chosen.get("allow", []),
            "conditional_strategy_families": chosen.get("conditional", []),
            "blocked_strategy_families": chosen.get("block", []),
            "position_multiplier": float(chosen.get("position_multiplier", 0)),
            "candidate_threshold_delta": float(chosen.get("candidate_threshold_delta", 0)),
            "max_new_positions_override": chosen.get("max_new_positions_override"),
            "route_reasons": [f"matched:{chosen['id']}", f"market={ctx['market_regime']}", f"sector={ctx['sector_strength']}"],
            "data_quality": {"state": "ok" if "UNKNOWN" not in chosen["id"] else "degraded"},
        }
        if self.policy_loader is not None:
            decision = self.policy_loader.authorize_entry(signal or {}, market_context=market_context,
                                                         sector_context=sector_context, as_of=as_of)
            result["policy_decision"] = decision
            if self.audit is not None:
                context = dict(audit_context or {})
                context.setdefault("phase", "DESCRIPTIVE_ROUTER")
                context.setdefault("run_id", str(uuid.uuid4()))
                context.setdefault("trace_id", str(uuid.uuid4()))
                self.audit.write_event(event_type="POLICY_DECISION", **context,
                    producer={"type": "strategy", "id": "strategy-router"},
                    status="ok" if decision["allowed"] else "reject",
                    payload={"boundary": "router", "policy_decision": decision})
            if not decision["allowed"]:
                families = set(result["allowed_strategy_families"] + result["conditional_strategy_families"])
                result["blocked_strategy_families"] = sorted(set(result["blocked_strategy_families"]) | (families - {"exit_defensive"}))
                result["allowed_strategy_families"] = [f for f in result["allowed_strategy_families"] if f == "exit_defensive"]
                result["conditional_strategy_families"] = []
                result["route_reasons"].append("policy:" + decision["reason"])
        return result
