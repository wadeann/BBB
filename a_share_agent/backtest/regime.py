from __future__ import annotations

from statistics import mean
# ── v0.8+: deterministic 7-state regime from multi-factor inputs ──────


def _sma(vals: list[float], n: int) -> float | None:
    """Simple moving average of last n values."""
    if not vals or n <= 0:
        return None
    recent = vals[-n:]
    return mean(recent) if len(recent) >= n else None


REGIME_7STATE = frozenset({
    "BULL_TREND", "BULL_VOLATILE", "ROTATION", "SIDEWAYS",
    "BEAR", "PANIC", "RECOVERY",
})

LIFECYCLE_7STATE = frozenset({
    "EMERGING", "ACCELERATING", "LEADING", "MATURE",
    "DISTRIBUTING", "FADING", "UNKNOWN",
})


def determine_regime_7state(
    *,
    index_trend: str,           # "up" | "down" | "range"
    index_ma_slope: float,      # slope of MA (e.g. ma20 slope)
    close_vs_ma: float,         # close / ma - 1
    ret5: float,                # 5-day return
    ret20: float,               # 20-day return
    drawdown20: float,          # 20-day max drawdown
    breadth_ratio: float | None,  # advancing / (advancing + declining)
    adv_decline_ratio: float | None,  # adv / decl (raw)
    new_high_count: int | None,    # stocks hitting new highs
    new_low_count: int | None,     # stocks hitting new lows
    vol_estimate: float | None,    # VIX-like volatility estimate
) -> dict[str, Any]:
    """Deterministic 7-state regime from multi-factor inputs.

    Returns dict with keys:
      regime (str), confidence (float), reasons (list[str])
    """
    reasons: list[str] = []
    confidence: float = 0.5

    # ── guard: we need at least core index data ──
    if index_trend == "unknown" or ret20 is None or drawdown20 is None:
        return {"regime": "UNKNOWN", "confidence": 0.0, "reasons": ["insufficient_index_data"]}

    # Determine candidate regime from index price action
    if ret20 > 0.08 and drawdown20 > -0.05:
        bull_candidate = "BULL_TREND"
        bull_conf = 0.85
        reasons.append("index_strong_uptrend")
    elif ret5 > 0.03 and ret20 > 0.05 and drawdown20 > -0.08:
        bull_candidate = "BULL_TREND"
        bull_conf = 0.75
        reasons.append("index_moderate_uptrend")
    elif ret20 > 0.03 and drawdown20 < -0.04:
        bull_candidate = "BULL_VOLATILE"
        bull_conf = 0.70
        reasons.append("index_uptrend_with_drawdown")
    elif ret5 < -0.06 and drawdown20 < -0.10:
        bull_candidate = "PANIC"
        bull_conf = 0.90
        reasons.append("index_sharp_decline")
    elif ret20 < -0.05 and drawdown20 < -0.08:
        bull_candidate = "BEAR"
        bull_conf = 0.80
        reasons.append("index_sustained_decline")
    elif ret5 < -0.03 and drawdown20 < -0.05:
        bull_candidate = "PANIC"
        bull_conf = 0.70
        reasons.append("index_moderate_decline")
    elif drawdown20 < -0.08 and ret5 > 0.03:
        bull_candidate = "RECOVERY"
        bull_conf = 0.75
        reasons.append("index_recovery_from_drawdown")
    elif abs(ret5) < 0.015 and abs(ret20) < 0.03:
        bull_candidate = "SIDEWAYS"
        bull_conf = 0.80
        reasons.append("index_low_momentum")
    elif index_trend == "range" and (ret5 * ret20) < 0:
        bull_candidate = "ROTATION"
        bull_conf = 0.65
        reasons.append("index_direction_change")
    elif close_vs_ma < -0.02 and ret5 > 0.035:
        bull_candidate = "RECOVERY"
        bull_conf = 0.65
        reasons.append("index_recovery_bounce")
    else:
        bull_candidate = "SIDEWAYS"
        bull_conf = 0.60
        reasons.append("index_default_sideways")

    # ── Breadth confirmation ──
    breadth_signal = 0.0
    if breadth_ratio is not None:
        if breadth_ratio > 0.6:
            breadth_signal = 0.1
            reasons.append("breadth_strong")
        elif breadth_ratio > 0.55:
            breadth_signal = 0.05
            reasons.append("breadth_positive")
        elif breadth_ratio < 0.4:
            breadth_signal = -0.1
            reasons.append("breadth_weak")
        elif breadth_ratio < 0.45:
            breadth_signal = -0.05
            reasons.append("breadth_negative")

    # ── New-high / new-low confirmation ──
    nh_nl_signal = 0.0
    if new_high_count is not None and new_low_count is not None:
        nh_nl_total = new_high_count + new_low_count
        if nh_nl_total > 0:
            nh_ratio_val = new_high_count / nh_nl_total
            if nh_ratio_val > 0.7:
                nh_nl_signal = 0.10
                reasons.append("new_highs_dominant")
            elif nh_ratio_val < 0.3:
                nh_nl_signal = -0.10
                reasons.append("new_lows_dominant")
            elif nh_ratio_val > 0.55:
                nh_nl_signal = 0.05
                reasons.append("new_highs_positive")

    # ── Volatility adjustment ──
    vol_signal = 0.0
    if vol_estimate is not None:
        if vol_estimate > 0.35:
            vol_signal = -0.10
            reasons.append("elevated_volatility")
            # PANIC/ROTATION maintain confidence
            if bull_candidate not in ("PANIC", "ROTATION"):
                reasons.append("volatility_discounts_confidence")
        elif vol_estimate > 0.25:
            vol_signal = -0.05
            reasons.append("moderate_volatility")

    # ── Resolve ──
    adjusted_conf = min(1.0, max(0.0, bull_conf + breadth_signal + nh_nl_signal + vol_signal))
    confidence = round(adjusted_conf, 2)

    return {
        "regime": bull_candidate,
        "confidence": confidence,
        "reasons": reasons,
    }


def determine_lifecycle_7state(
    *,
    breadth_ratio: float | None,
    leader_count: int | None,
    turnover_share: float | None,
    persistence: int,
    expansion: int | None,
    concentration: float | None,
    sector_strength_mean: float | None = None,
) -> dict[str, Any]:
    """Deterministic 7-state lifecycle from breadth and sector-level factors.

    Returns dict with keys: lifecycle (str), confidence (float), reasons (list[str]).
    """
    reasons: list[str] = []
    confidence: float = 0.5

    if breadth_ratio is None and sector_strength_mean is None:
        return {"lifecycle": "UNKNOWN", "confidence": 0.0, "reasons": ["insufficient_data"]}

    # Score on multiple dimensions
    expansion_score = 0
    if expansion is not None:
        if expansion >= 8:
            expansion_score = 2
            reasons.append("broad_sector_expansion")
        elif expansion >= 5:
            expansion_score = 1
            reasons.append("moderate_sector_expansion")
        elif expansion <= 2:
            expansion_score = -1
            reasons.append("narrow_sector_expansion")

    breadth_score = 0
    if breadth_ratio is not None:
        if breadth_ratio > 0.6:
            breadth_score = 2
            reasons.append("broad_breadth")
        elif breadth_ratio > 0.55:
            breadth_score = 1
            reasons.append("positive_breadth")
        elif breadth_ratio < 0.4:
            breadth_score = -2
            reasons.append("narrow_breadth")
        elif breadth_ratio < 0.45:
            breadth_score = -1
            reasons.append("weak_breadth")

    leader_score = 0
    if leader_count is not None:
        if leader_count >= 4:
            leader_score = 2
            reasons.append("many_leaders")
        elif leader_count >= 2:
            leader_score = 1
            reasons.append("some_leaders")
        elif leader_count < 1:
            leader_score = -1
            reasons.append("few_leaders")

    turnover_score = 0
    if turnover_share is not None:
        if turnover_share > 0.4:
            turnover_score = 1
            reasons.append("high_top3_turnover")
        elif turnover_share > 0.3:
            turnover_score = 0
        elif turnover_share < 0.2:
            turnover_score = -1
            reasons.append("low_top3_turnover")

    concentration_score = 0
    if concentration is not None:
        if concentration > 0.25:
            concentration_score = 0
            reasons.append("concentrated_market")
        elif concentration > 0.15:
            concentration_score = 0
        else:
            concentration_score = 1
            reasons.append("broadly_distributed")

    persistence_score = 0
    if persistence >= 10:
        persistence_score = 2
        reasons.append("long_persistence")
    elif persistence >= 5:
        persistence_score = 1
        reasons.append("moderate_persistence")

    total = expansion_score + breadth_score + leader_score + turnover_score + concentration_score + persistence_score

    # Map aggregate score to lifecycle
    if total >= 7:
        lifecycle = "LEADING"
        confidence = min(0.9, 0.5 + total * 0.05)
        reasons.append("leading_conditions")
    elif total >= 4:
        lifecycle = "ACCELERATING"
        confidence = min(0.8, 0.5 + total * 0.05)
        reasons.append("accelerating_conditions")
    elif total >= 1:
        lifecycle = "EMERGING"
        confidence = min(0.7, 0.5 + total * 0.05)
        reasons.append("emerging_conditions")
    elif total >= -2:
        lifecycle = "MATURE"
        confidence = 0.6
        reasons.append("mature_conditions")
    elif total >= -4:
        lifecycle = "DISTRIBUTING"
        confidence = 0.65
        reasons.append("distributing_conditions")
    else:
        lifecycle = "FADING"
        confidence = 0.75
        reasons.append("fading_conditions")

    return {
        "lifecycle": lifecycle,
        "confidence": round(confidence, 2),
        "reasons": reasons,
    }


# ── Confidence / data quality ──────────────────────────────────────────


def compute_regime_confidence(
    regime_result: dict[str, Any],
    *,
    has_breadth: bool = False,
    has_nh_nl: bool = False,
    has_vol: bool = False,
) -> float:
    """Adjust regime confidence based on signal consistency and data coverage."""
    base = regime_result.get("confidence", 0.0)
    signal_count = sum([has_breadth, has_nh_nl, has_vol])
    if signal_count >= 2:
        return round(min(1.0, base + 0.10), 2)
    elif signal_count == 1:
        return round(min(1.0, base + 0.05), 2)
    return round(base, 2)


def compute_data_quality(
    *,
    has_index_data: bool = True,
    breadth_coverage: float = 1.0,
    has_sector_data: bool = True,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Data quality assessment.

    Returns {"state": str, "reasons": list[str], "coverage": float}.
    state ∈ {"ok", "degraded", "unknown"}.
    coverage ∈ [0.0, 1.0] — fraction of expected data available.
    """
    r = list(reasons or [])

    if not has_index_data:
        return {"state": "unknown", "reasons": r + ["index_data_unavailable"], "coverage": 0.0}

    coverage = breadth_coverage
    if not has_sector_data:
        coverage = min(coverage, 0.5)
        r.append("sector_data_missing")

    state = "ok"
    if coverage < 0.5:
        state = "unknown"
        r.append("insufficient_coverage")
    elif coverage < 0.9:
        state = "degraded"
        r.append("partial_coverage")

    return {"state": state, "reasons": r, "coverage": round(coverage, 4)}


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
            regime = "BULL_TREND"; confidence = 0.90; reasons.append("strong_momentum_low_drawdown")
        elif ret20 > 0.08 and dd20 > -0.08:
            regime = "BULL_TREND"; confidence = 0.80; reasons.append("uptrend_moderate_drawdown")
        elif dd20 < -0.04:
            regime = "BULL_VOLATILE"; confidence = 0.75; reasons.append("uptrend_with_volatility")
        else:
            regime = "BULL_TREND"; confidence = 0.70; reasons.append("risk_on_default")
    elif legacy_regime == "risk_off":
        if ret5 < -0.06 and dd20 < -0.10:
            regime = "PANIC"; confidence = 0.90; reasons.append("sharp_decline_deep_drawdown")
        elif ret20 < -0.05 and dd20 < -0.08:
            regime = "BEAR"; confidence = 0.85; reasons.append("sustained_decline")
        elif sentiment == "panic" or (ret5 < -0.04 and dd20 < -0.06):
            regime = "PANIC"; confidence = 0.80; reasons.append("sentiment_panic")
        else:
            regime = "BEAR"; confidence = 0.70; reasons.append("risk_off_default")
    else:
        if sentiment == "rebound":
            regime = "RECOVERY"; confidence = 0.75; reasons.append("rebound_sentiment")
        elif abs(ret5) < 0.015 and abs(ret20) < 0.03:
            regime = "SIDEWAYS"; confidence = 0.85; reasons.append("low_momentum")
        elif abs(ret20) < 0.05 and (ret5 * ret20) < 0:
            regime = "ROTATION"; confidence = 0.70; reasons.append("direction_changes")
        else:
            regime = "SIDEWAYS"; confidence = 0.60; reasons.append("neutral_default")

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "input_metrics": metrics,
        "reason_codes": reasons,
        "trend": trend,
        "sentiment": sentiment,
    }



def _expand_lifecycle(legacy_lifecycle: str, ret5: float, ret20: float,
                      score: float, strength: str) -> dict[str, Any]:
    metrics = {"ret5": ret5, "ret20": ret20, "score": score,
               "legacy_lifecycle": legacy_lifecycle, "strength": strength}
    reasons: list[str] = []
    if legacy_lifecycle == "accelerating":
        if ret5 > 0.05 and score >= 75:
            lifecycle = "ACCELERATING"; confidence = 0.85; reasons.append("strong_acceleration")
        else:
            lifecycle = "EMERGING"; confidence = 0.70; reasons.append("moderate_acceleration_mapped_to_emerging")
    elif legacy_lifecycle == "emerging":
        if ret20 > 0.08:
            lifecycle = "ACCELERATING"; confidence = 0.75; reasons.append("emerging_with_momentum")
        else:
            lifecycle = "EMERGING"; confidence = 0.70; reasons.append("early_emerging")
    elif legacy_lifecycle == "crowded":
        if ret5 > 0.03 and score >= 65:
            lifecycle = "LEADING"; confidence = 0.70; reasons.append("crowded_but_strong")
        elif ret5 < -0.02:
            lifecycle = "DISTRIBUTING"; confidence = 0.75; reasons.append("crowded_weakening")
        else:
            lifecycle = "MATURE"; confidence = 0.65; reasons.append("crowded_stable")
    elif legacy_lifecycle == "cooling":
        if ret20 > 0.03 and strength == "strong":
            lifecycle = "MATURE"; confidence = 0.70; reasons.append("cooling_but_strong_medium_term")
        elif ret5 < -0.03:
            lifecycle = "FADING"; confidence = 0.85; reasons.append("sharp_cooling")
        else:
            lifecycle = "DISTRIBUTING"; confidence = 0.70; reasons.append("moderate_cooling")
    else:
        lifecycle = "UNKNOWN"; confidence = 0.0; reasons.append("unknown_lifecycle_no_data")
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
        # Strict diagnostic fallback is a legacy compatibility surface; downstream
        # routing treats either case as unknown and fail-closed.
        return {"as_of":as_of,"sector":name,"sector_strength":"unknown",
                "sector_lifecycle":"unknown","lifecycle":"unknown",
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
