from __future__ import annotations

from typing import Any

from ..utils import now_shanghai


class StrategyRouter:
    def __init__(self, config: dict[str, Any]):
        self.cfg = config
        self.routes = {r["id"]: r for r in config.get("routes", [])}

    @staticmethod
    def _matches(route: dict[str, Any], ctx: dict[str, Any]) -> bool:
        for k, v in route.get("when", {}).items():
            if ctx.get(k) != v:
                return False
        return True

    def route(self, *, market_context: dict[str, Any], sector_context: dict[str, Any]) -> dict[str, Any]:
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
        return {
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
