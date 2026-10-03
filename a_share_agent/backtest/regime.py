from __future__ import annotations

from statistics import mean
from typing import Any


def _sma(vals: list[float], n: int) -> float | None:
    return mean(vals[-n:]) if len(vals) >= n else None


# ── v0.8: expanded market regime taxonomy ──────────────────────────────
# Legacy → expanded mapping with backward compatibility.
# New regime values: BULL_TREND, BULL_VOLATILE, ROTATION, SIDEWAYS,
#                     BEAR, PANIC, RECOVERY

def _expand_regime(legacy_regime: str, trend: str, sentiment: str,
                   ret5: float, ret20: float, dd20: float, ma20: float,
                   close: float) -> dict[str, Any]:
    """Map legacy risk_on/risk_off/neutral to expanded regime taxonomy."""
    metrics = {
        "close": close, "ma20": ma20, "ret5": ret5, "ret20": ret20,
        "drawdown20": dd20, "legacy_regime": legacy_regime,
        "legacy_trend": trend, "legacy_sentiment": sentiment,
    }
    reasons: list[str] = []

    if legacy_regime == "risk_on":
        if ret5 > 0.08 and dd20 > -0.03:
            regime = "BULL_TREND"
            confidence = 0.90
            reasons.append("strong_momentum_low_drawdown")
        elif ret20 > 0.08 and dd20 > -0.08:
            regime = "BULL_TREND"
            confidence = 0.80
            reasons.append("uptrend_moderate_drawdown")
        elif dd20 < -0.04:
            regime = "BULL_VOLATILE"
            confidence = 0.75
            reasons.append("uptrend_with_volatility")
        else:
            regime = "BULL_TREND"
            confidence = 0.70
            reasons.append("risk_on_default")
    elif legacy_regime == "risk_off":
        if ret5 < -0.06 and dd20 < -0.10:
            regime = "PANIC"
            confidence = 0.90
            reasons.append("sharp_decline_deep_drawdown")
        elif ret20 < -0.05 and dd20 < -0.08:
            regime = "BEAR"
            confidence = 0.85
            reasons.append("sustained_decline")
        elif sentiment == "panic" or (ret5 < -0.04 and dd20 < -0.06):
            regime = "PANIC"
            confidence = 0.80
            reasons.append("sentiment_panic")
        else:
            regime = "BEAR"
            confidence = 0.70
            reasons.append("risk_off_default")
    else:
        if sentiment == "rebound":
            regime = "RECOVERY"
            confidence = 0.75
            reasons.append("rebound_sentiment")
        elif abs(ret5) < 0.015 and abs(ret20) < 0.03:
            regime = "SIDEWAYS"
            confidence = 0.85
            reasons.append("low_momentum")
        elif abs(ret20) < 0.05 and (ret5 * ret20) < 0:
            regime = "ROTATION"
            confidence = 0.70
            reasons.append("direction_changes")
        else:
            regime = "SIDEWAYS"
            confidence = 0.60
            reasons.append("neutral_default")

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "input_metrics": metrics,
        "reason_codes": reasons,
        "trend": trend,
        "sentiment": sentiment,
    }


def market_context_from_history(bars: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    hist = [b for b in bars if b["date"] <= as_of]
    if len(hist) < 25:
        return {"market_regime":"unknown","market_trend":"unknown","sentiment_phase":"unknown",
                "regime":"unknown","regime_confidence":0.0,"regime_reasons":[],
                "as_of":as_of,"data_quality":{"state":"degraded"}}
    closes = [float(b["close"]) for b in hist]
    ma20 = _sma(closes, 20) or closes[-1]
    prev_ma20 = mean(closes[-25:-5]) if len(closes) >= 25 else ma20
    slope = ma20 / prev_ma20 - 1 if prev_ma20 else 0
    ret5 = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0
    ret20 = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0
    dd20 = closes[-1] / max(closes[-20:]) - 1
    if closes[-1] > ma20 and slope > 0.005 and ret20 > 0:
        legacy_regime, trend = "risk_on", "up"
    elif closes[-1] < ma20 and slope < -0.005 and ret20 < 0:
        legacy_regime, trend = "risk_off", "down"
    else:
        legacy_regime, trend = "neutral", "range"
    if dd20 < -0.08 and ret5 > 0.035:
        sentiment = "rebound"
    elif ret5 > 0.04 and ret20 > 0.08:
        sentiment = "hot"
    elif ret5 < -0.04:
        sentiment = "cooling" if legacy_regime != "risk_off" else "panic"
    else:
        sentiment = "warming" if ret5 > 0 else "divergent"
    expanded = _expand_regime(legacy_regime, trend, sentiment, ret5, ret20, dd20, ma20, closes[-1])
    return {
        "as_of": as_of,
        "market_regime": legacy_regime,
        "market_trend": trend,
        "sentiment_phase": sentiment,
        "regime": expanded["regime"],
        "regime_confidence": expanded["confidence"],
        "regime_reasons": expanded["reason_codes"],
        "regime_metrics": expanded["input_metrics"],
        "evidence": {"close": closes[-1], "ma20": ma20, "ma20_slope_proxy": slope,
                     "ret5":ret5,"ret20":ret20,"drawdown20":dd20},
        "data_quality": {"state":"ok","historical_price_derived":True},
    }


def _expand_lifecycle(legacy_lifecycle: str, ret5: float, ret20: float,
                      score: float, strength: str) -> dict[str, Any]:
    """Map legacy lifecycle to expanded 6-stage model."""
    metrics = {"ret5": ret5, "ret20": ret20, "score": score,
               "legacy_lifecycle": legacy_lifecycle, "strength": strength}
    reasons: list[str] = []

    if legacy_lifecycle == "accelerating":
        if ret5 > 0.05 and score >= 75:
            lifecycle = "ACCELERATING"
            confidence = 0.85
            reasons.append("strong_acceleration")
        else:
            lifecycle = "EMERGING"
            confidence = 0.70
            reasons.append("moderate_acceleration_mapped_to_emerging")
    elif legacy_lifecycle == "emerging":
        if ret20 > 0.08:
            lifecycle = "ACCELERATING"
            confidence = 0.75
            reasons.append("emerging_with_momentum")
        else:
            lifecycle = "EMERGING"
            confidence = 0.70
            reasons.append("early_emerging")
    elif legacy_lifecycle == "crowded":
        if ret5 > 0.03 and score >= 65:
            lifecycle = "LEADING"
            confidence = 0.70
            reasons.append("crowded_but_strong")
        elif ret5 < -0.02:
            lifecycle = "DISTRIBUTING"
            confidence = 0.75
            reasons.append("crowded_weakening")
        else:
            lifecycle = "MATURE"
            confidence = 0.65
            reasons.append("crowded_stable")
    elif legacy_lifecycle == "cooling":
        if ret20 > 0.03 and strength == "strong":
            lifecycle = "MATURE"
            confidence = 0.70
            reasons.append("cooling_but_strong_medium_term")
        elif ret5 < -0.03:
            lifecycle = "FADING"
            confidence = 0.85
            reasons.append("sharp_cooling")
        else:
            lifecycle = "DISTRIBUTING"
            confidence = 0.70
            reasons.append("moderate_cooling")
    else:
        lifecycle = "UNKNOWN"
        confidence = 0.0
        reasons.append("unknown_lifecycle_no_data")

    return {
        "lifecycle": lifecycle,
        "confidence": round(confidence, 2),
        "metrics": metrics,
        "reason_codes": reasons,
    }


def sector_context_from_history(bars: list[dict[str, Any]], as_of: str, *,
                                 name: str | None = None,
                                 fallback_neutral: bool = True) -> dict[str, Any]:
    hist = [b for b in bars if b.get("date", "") <= as_of]
    if len(hist) < 25:
        if fallback_neutral:
            return {"as_of":as_of,"sector":name,"sector_strength":"neutral",
                    "sector_lifecycle":"unknown","lifecycle":"UNKNOWN",
                    "lifecycle_confidence":0.0,"lifecycle_reasons":["insufficient_history_fallback"],
                    "data_quality":{"state":"degraded","fallback":"neutral_no_history","reason":"insufficient_history"}}
        return {"as_of":as_of,"sector":name,"sector_strength":"unknown",
                "sector_lifecycle":"unknown","lifecycle":"UNKNOWN",
                "lifecycle_confidence":0.0,"lifecycle_reasons":[],
                "data_quality":{"state":"degraded"}}
    closes=[float(b["close"]) for b in hist]
    ma20=mean(closes[-20:]); prev=mean(closes[-25:-5]); slope=ma20/prev-1 if prev else 0
    ret5=closes[-1]/closes[-6]-1; ret20=closes[-1]/closes[-21]-1
    score=50
    score += 20 if closes[-1] > ma20 else -20
    score += max(-20, min(20, ret20*200))
    score += max(-10, min(10, slope*500))
    if score >= 70: strength="strong"
    elif score < 45: strength="weak"
    else: strength="neutral"
    if ret20 > .08 and ret5 > .02: legacy_lifecycle="accelerating"
    elif ret20 > .03 and ret5 > 0: legacy_lifecycle="emerging"
    elif ret20 > .05 and ret5 < -.02: legacy_lifecycle="cooling"
    elif ret20 < 0: legacy_lifecycle="cooling"
    else: legacy_lifecycle="crowded" if ret5 > .05 else "emerging"
    expanded = _expand_lifecycle(legacy_lifecycle, ret5, ret20, score, strength)
    return {
        "as_of":as_of,"sector":name,
        "sector_strength":strength,
        "sector_lifecycle":legacy_lifecycle,
        "lifecycle":expanded["lifecycle"],
        "lifecycle_confidence":expanded["confidence"],
        "lifecycle_reasons":expanded["reason_codes"],
        "sector_score":round(score,2),
        "evidence":{"ret5":ret5,"ret20":ret20,"ma20":ma20,"slope":slope},
        "data_quality":{"state":"ok","historical_price_derived":True,
                        "mainline_component":"unavailable_in_backtest"},
    }


def market_context_from_benchmarks(series: dict[str,list[dict[str,Any]]], as_of: str) -> dict[str,Any]:
    contexts={k:market_context_from_history(v,as_of) for k,v in series.items() if v}
    valid=[x for x in contexts.values() if x.get("market_regime")!="unknown"]
    if not valid:
        return {"market_regime":"unknown","market_trend":"unknown","sentiment_phase":"unknown",
                "regime":"unknown","regime_confidence":0.0,"regime_reasons":[],
                "as_of":as_of,"benchmarks":contexts,"data_quality":{"state":"degraded"}}
    regs=[x["market_regime"] for x in valid]
    if regs.count("risk_on")>=2 or (len(valid)==1 and regs[0]=="risk_on"): legacy_regime="risk_on"
    elif regs.count("risk_off")>=2 or (len(valid)==1 and regs[0]=="risk_off"): legacy_regime="risk_off"
    else: legacy_regime="neutral"
    trends=[x["market_trend"] for x in valid]
    trend="up" if trends.count("up")>=2 else ("down" if trends.count("down")>=2 else "range")
    sentiments=[x["sentiment_phase"] for x in valid]
    sentiment=max(set(sentiments),key=sentiments.count) if sentiments else "unknown"
    expanded_regimes=[x.get("regime","unknown") for x in valid if x.get("regime")]
    regime=max(set(expanded_regimes),key=expanded_regimes.count) if expanded_regimes else "unknown"
    regime_conf=max(x.get("regime_confidence",0.0) for x in valid if x.get("regime")==regime)
    regime_reasons=list(dict.fromkeys(
        r for x in valid if x.get("regime")==regime
        for r in x.get("regime_reasons",[])))
    return {
        "as_of":as_of,
        "market_regime":legacy_regime,
        "market_trend":trend,
        "sentiment_phase":sentiment,
        "regime":regime,
        "regime_confidence":round(regime_conf,2),
        "regime_reasons":regime_reasons,
        "benchmarks":contexts,
        "data_quality":{"state":"ok","historical_price_derived":True,
                        "benchmark_count":len(valid)},
    }
