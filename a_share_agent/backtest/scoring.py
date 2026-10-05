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

def v4_score(
    hits: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    market: dict[str, Any],
    sector: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """v4 100-point scoring card.

    Returns (score: float, breakdown: dict[str, float])
    """
    # 1. 供需逻辑/催化 (0-20): base 10 + 2 per primary signal
    n_prim = sum(1 for h in hits if h.get("strength") == "primary")
    supply_demand = min(20, 10 + n_prim * 2)

    # 2. 板块共振 (0-20): from sector context
    sector_strength = sector.get("sector_strength", "unknown")
    sector_resonance = {"strong": 18, "neutral": 12, "weak": 6, "unknown": 8}.get(sector_strength, 8)

    # 3. 趋势结构 (0-20): from market regime + MA alignment
    regime = market.get("market_regime", "unknown")
    regime_score = {"risk_on": 16, "neutral": 12, "risk_off": 6}.get(regime, 8)

    # 4. 资金筹码 (0-15): infer from volume pattern
    vol_evidence = [
        h.get("evidence", {}).get("volume_ratio", 1)
        for h in hits
        if "volume_ratio" in h.get("evidence", {})
    ]
    avg_vol = sum(vol_evidence) / len(vol_evidence) if vol_evidence else 1
    fund_score = min(15, 5 + (avg_vol if avg_vol > 1.5 else 0) * 3)

    # 5. 量价形态 (0-15): from signal quality
    confidence_hits = len([h for h in hits if h.get("strength") in ("primary", "confirmation")])
    pattern_score = min(15, 8 + confidence_hits * 2)

    # 6. 消息/基本面 (0-10): neutral default (no real-time news in backtest)
    fundamental = 7  # neutral positive

    breakdown = {
        "supply_demand": round(supply_demand, 1),
        "sector_resonance": round(sector_resonance, 1),
        "trend_structure": round(regime_score, 1),
        "fund_chip": round(fund_score, 1),
        "volume_price": round(pattern_score, 1),
        "fundamental": round(fundamental, 1),
    }
    score = sum(breakdown.values())
    return round(min(100, score), 2), breakdown
