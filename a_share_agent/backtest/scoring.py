from __future__ import annotations

from typing import Any


PRIMARY_BASE = {
    "triple_golden_cross": 80,
    "ma_convergence_breakout": 82,
    "ma60_breakout_retest": 80,
    "single_bull_hold": 78,
    "high_volume_breakout": 79,
    "ma5_momentum_pullback": 78,
}


def deterministic_score(hits: list[dict[str, Any]], bars: list[dict[str, Any]], market: dict[str, Any], sector: dict[str, Any]) -> tuple[float, dict[str, float]]:
    prim = [h for h in hits if h.get("strength") == "primary"]
    conf = [h for h in hits if h.get("strength") == "confirmation"]
    if not prim:
        return 0.0, {"trend":0,"volume_price":0,"pattern":0,"fundamental":0,"market":0,"sector":0,"liquidity":0}
    base=max(PRIMARY_BASE.get(str(h.get("signal")), 76) for h in prim)
    extra=min(8, max(0,len(prim)-1)*4 + len(conf)*2)
    m = 4 if market.get("market_regime") == "risk_on" else (2 if market.get("market_regime") == "neutral" else 0)
    s = 6 if sector.get("sector_strength") == "strong" else (3 if sector.get("sector_strength") == "neutral" else 0)
    # fundamental is neutral/not used in historical deterministic mode; do not fabricate it.
    score=min(100.0, base + extra + (m-2) + (s-3))
    trend=min(25.0, 18 + len(prim)*2)
    vp=min(25.0, 17 + sum(1 for h in prim if h.get("signal") in {"high_volume_breakout","triple_golden_cross","ma_convergence_breakout"})*3)
    pattern=min(20.0, 14 + len(conf)*2)
    breakdown={"trend":trend,"volume_price":vp,"pattern":pattern,"fundamental":7.5,"market":float(m),"sector":float(s),"liquidity":4.0}
    return round(score,2), breakdown
