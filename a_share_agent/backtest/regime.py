from __future__ import annotations

from statistics import mean
from typing import Any


def _sma(vals: list[float], n: int) -> float | None:
    return mean(vals[-n:]) if len(vals) >= n else None


def market_context_from_history(bars: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    hist = [b for b in bars if b["date"] <= as_of]
    if len(hist) < 25:
        return {"market_regime":"unknown","market_trend":"unknown","sentiment_phase":"unknown","as_of":as_of,"data_quality":{"state":"degraded"}}
    closes = [float(b["close"]) for b in hist]
    ma20 = _sma(closes, 20) or closes[-1]
    prev_ma20 = mean(closes[-25:-5]) if len(closes) >= 25 else ma20
    slope = ma20 / prev_ma20 - 1 if prev_ma20 else 0
    ret5 = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0
    ret20 = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0
    dd20 = closes[-1] / max(closes[-20:]) - 1
    if closes[-1] > ma20 and slope > 0.005 and ret20 > 0:
        regime, trend = "risk_on", "up"
    elif closes[-1] < ma20 and slope < -0.005 and ret20 < 0:
        regime, trend = "risk_off", "down"
    else:
        regime, trend = "neutral", "range"
    if dd20 < -0.08 and ret5 > 0.035:
        sentiment = "rebound"
    elif ret5 > 0.04 and ret20 > 0.08:
        sentiment = "hot"
    elif ret5 < -0.04:
        sentiment = "cooling" if regime != "risk_off" else "panic"
    else:
        sentiment = "warming" if ret5 > 0 else "divergent"
    return {
        "as_of": as_of, "market_regime": regime, "market_trend": trend,
        "sentiment_phase": sentiment,
        "evidence": {"close": closes[-1], "ma20": ma20, "ma20_slope_proxy": slope, "ret5":ret5, "ret20":ret20, "drawdown20":dd20},
        "data_quality": {"state":"ok","historical_price_derived":True},
    }


def sector_context_from_history(bars: list[dict[str, Any]], as_of: str, *, name: str | None = None, fallback_neutral: bool = True) -> dict[str, Any]:
    hist = [b for b in bars if b.get("date", "") <= as_of]
    if len(hist) < 25:
        if fallback_neutral:
            return {"as_of":as_of,"sector":name,"sector_strength":"neutral","sector_lifecycle":"unknown","data_quality":{"state":"degraded","fallback":"neutral_no_history"}}
        return {"as_of":as_of,"sector":name,"sector_strength":"unknown","sector_lifecycle":"unknown","data_quality":{"state":"degraded"}}
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
    if ret20 > .08 and ret5 > .02: lifecycle="accelerating"
    elif ret20 > .03 and ret5 > 0: lifecycle="emerging"
    elif ret20 > .05 and ret5 < -.02: lifecycle="cooling"
    elif ret20 < 0: lifecycle="cooling"
    else: lifecycle="crowded" if ret5 > .05 else "emerging"
    return {"as_of":as_of,"sector":name,"sector_strength":strength,"sector_lifecycle":lifecycle,
            "sector_score":round(score,2),"evidence":{"ret5":ret5,"ret20":ret20,"ma20":ma20,"slope":slope},
            "data_quality":{"state":"ok","historical_price_derived":True,"mainline_component":"unavailable_in_backtest"}}


def market_context_from_benchmarks(series: dict[str,list[dict[str,Any]]], as_of: str) -> dict[str,Any]:
    contexts={k:market_context_from_history(v,as_of) for k,v in series.items() if v}
    valid=[x for x in contexts.values() if x.get("market_regime")!="unknown"]
    if not valid:
        return {"market_regime":"unknown","market_trend":"unknown","sentiment_phase":"unknown","as_of":as_of,"benchmarks":contexts,"data_quality":{"state":"degraded"}}
    regs=[x["market_regime"] for x in valid]
    if regs.count("risk_on")>=2 or (len(valid)==1 and regs[0]=="risk_on"): regime="risk_on"
    elif regs.count("risk_off")>=2 or (len(valid)==1 and regs[0]=="risk_off"): regime="risk_off"
    else: regime="neutral"
    trends=[x["market_trend"] for x in valid]
    trend="up" if trends.count("up")>=2 else ("down" if trends.count("down")>=2 else "range")
    sentiments=[x["sentiment_phase"] for x in valid]
    sentiment=max(set(sentiments),key=sentiments.count) if sentiments else "unknown"
    return {"as_of":as_of,"market_regime":regime,"market_trend":trend,"sentiment_phase":sentiment,"benchmarks":contexts,"data_quality":{"state":"ok","historical_price_derived":True,"benchmark_count":len(valid)}}
