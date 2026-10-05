from __future__ import annotations
from typing import Any


def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) >= n:
        return sum(vals[-n:]) / n
    return None


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _ema(vals: list[float], n: int) -> list[float]:
    """Exponential moving average, full series."""
    if not vals:
        return []
    a = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def _macd(vals: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Return (dif, dea, macd_bar) for 12/26/9 MACD over full series."""
    e12 = _ema(vals, 12)
    e26 = _ema(vals, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = _ema(dif, 9)
    bar = [2 * (d - s) for d, s in zip(dif, dea)]
    return dif, dea, bar


def four_dimension_reversal_check(
    bars: list[dict[str, Any]],
    symbol: str = "",
    as_of: str = "",
) -> dict[str, Any]:
    """Check if a stock shows reversal signal using v4 4-dimension framework.

    Returns
    -------
    dict with keys:
        signal: "reversal" / "rebound" / "none"
        confidence: float 0..1 combined from passing dimensions
        checks: dict of per-dimension results
        verdict: bool — True if enough dimensions pass to warrant attention
    """
    if len(bars) < 30:
        return {
            "signal": "none",
            "confidence": 0.0,
            "verdict": False,
            "checks": {
                "dim1_ma": None,
                "dim2_fund": None,
                "dim3_kline": None,
                "dim4_chip": None,
            },
            "note": "insufficient bars (<30)",
        }

    c = [_f(x.get("close")) for x in bars]
    h = [_f(x.get("high")) for x in bars]
    l = [_f(x.get("low")) for x in bars]

    # ── Dimension 1: MA / MACD ──────────────────────────────────────────
    ma5 = _sma(c, 5)
    ma10 = _sma(c, 10)
    ma20 = _sma(c, 20)
    dif, dea, _macd_bar = _macd(c)
    dif_last = dif[-1] if dif else None
    dea_last = dea[-1] if dea else None
    dif_prev = dif[-2] if len(dif) >= 2 else None
    dea_prev = dea[-2] if len(dea) >= 2 else None

    dim1_ma: dict[str, Any] = {}

    if ma5 is not None and ma10 is not None and ma20 is not None:
        dim1_ma["ma5"] = round(ma5, 3)
        dim1_ma["ma10"] = round(ma10, 3)
        dim1_ma["ma20"] = round(ma20, 3)
        # Bearish arrangement: MA5 < MA10 < MA20  → fail
        dim1_ma["bearish_arrangement"] = bool(ma5 < ma10 < ma20)
    else:
        dim1_ma["bearish_arrangement"] = None

    # MACD underwater dead cross check
    if dif_last is not None and dea_last is not None:
        dim1_ma["dif"] = round(dif_last, 4)
        dim1_ma["dea"] = round(dea_last, 4)
        underwater = dif_last < 0 and dea_last < 0
        dead_cross = (
            dif_last < dea_last
            and dif_prev is not None
            and dea_prev is not None
            and dif_prev >= dea_prev
        )
        dim1_ma["underwater"] = underwater
        dim1_ma["dead_cross"] = dead_cross
        dim1_ma["macd_fail"] = underwater and dead_cross
    else:
        dim1_ma["macd_fail"] = None

    dim1_pass = not dim1_ma.get("bearish_arrangement", True) and not dim1_ma.get("macd_fail", False)

    # ── Dimension 2: Fund flow — skipped (data unavailable) ─────────────
    dim2_fund: dict[str, Any] | None = {
        "available": False,
        "note": "fund-flow data not available in bar-only input",
    }

    # ── Dimension 3: K-line reversal patterns ───────────────────────────
    dim3_kline: dict[str, Any] = {}
    lookback = min(30, len(bars))
    c30 = c[-lookback:]
    l30 = l[-lookback:]
    h30 = h[-lookback:]
    dif30 = dif[-lookback:] if len(dif) >= lookback else dif
    dea30 = dea[-lookback:] if len(dea) >= lookback else dea

    dim3_kline["lookback"] = lookback

    # ── 3a. Bottom divergence (底背驰) ──
    # Split lookback into two segments: older half (A) and newer half (B).
    # Divergence: segment B makes a lower low than segment A,
    # but MACD at the B-low is higher (non-confirmation).
    half = lookback // 2
    seg_a_close = c30[:half]
    seg_b_close = c30[half:]
    seg_a_low_idx = min(range(len(seg_a_close)), key=lambda i: seg_a_close[i]) if seg_a_close else 0
    seg_b_low_idx = min(range(len(seg_b_close)), key=lambda i: seg_b_close[i]) if seg_b_close else 0

    seg_a_low = seg_a_close[seg_a_low_idx] if seg_a_close else None
    seg_b_low = seg_b_close[seg_b_low_idx] if seg_b_close else None

    if seg_a_low is not None and seg_b_low is not None and seg_a_low > 0:
        price_lower_low = seg_b_low < seg_a_low * 0.995
        macd_a = dif30[seg_a_low_idx] if len(dif30) > seg_a_low_idx else None
        macd_b = dif30[half + seg_b_low_idx] if len(dif30) > half + seg_b_low_idx else None
        dea_a = dea30[seg_a_low_idx] if len(dea30) > seg_a_low_idx else None
        dea_b = dea30[half + seg_b_low_idx] if len(dea30) > half + seg_b_low_idx else None

        macd_higher = macd_a is not None and macd_b is not None and macd_b > macd_a
        dea_trend_up = dea_a is not None and dea_b is not None and dea_b > dea_a

        has_bottom_divergence = price_lower_low and (macd_higher or dea_trend_up)
        dim3_kline["divergence"] = {
            "seg_a_low": round(seg_a_low, 3),
            "seg_b_low": round(seg_b_low, 3),
            "price_lower_low": bool(price_lower_low),
            "macd_a": round(macd_a, 4) if macd_a is not None else None,
            "macd_b": round(macd_b, 4) if macd_b is not None else None,
            "dea_a": round(dea_a, 4) if dea_a is not None else None,
            "dea_b": round(dea_b, 4) if dea_b is not None else None,
            "macd_higher_low": macd_higher if macd_a is not None and macd_b is not None else None,
            "dea_trend_up": dea_trend_up if dea_a is not None and dea_b is not None else None,
        }
    else:
        has_bottom_divergence = False
        dim3_kline["divergence"] = None

    # ── 3b. Bottom fractal (底分型) ──
    # Last 3 bars: middle bar has lowest low, left > middle < right
    if len(bars) >= 3:
        mid_low = l[-2]
        left_low = l[-3]
        right_low = l[-1]
        mid_close = c[-2]
        fractal = left_low > mid_low < right_low
        right_higher_close = c[-1] > mid_close if c[-1] and mid_close else False
        dim3_kline["bottom_fractal"] = {
            "left_low": round(left_low, 3),
            "mid_low": round(mid_low, 3),
            "right_low": round(right_low, 3),
            "fractal_found": fractal,
            "right_higher_close": right_higher_close,
        }
        has_fractal = fractal and right_higher_close
    else:
        has_fractal = False
        dim3_kline["bottom_fractal"] = None

    # ── 3c. Rising segment (上升线段) ──
    # After the segment-B low, check if price is rising from that low
    seg_b_local_low = seg_b_low_idx  # index within segment B
    global_low_idx = half + seg_b_local_low  # index within c30
    rising_segment_found = False
    if global_low_idx < len(c30) - 3:
        post_low = c30[global_low_idx + 1:]
        if len(post_low) >= 3:
            early_low = min(post_low[:3])
            late_low = min(post_low[-3:])
            if late_low > early_low * 1.002:
                rising_segment_found = True
        # Alternatively, latest close is well above the low
        if c[-1] > seg_b_low * 1.03:
            rising_segment_found = True
    dim3_kline["rising_segment"] = {
        "seg_b_low": round(seg_b_low, 3) if seg_b_low is not None else None,
        "latest_close": round(c[-1], 3),
        "rising_from_low": rising_segment_found,
    }

    # Combined K-line verdict
    kline_positive_count = sum([has_bottom_divergence, has_fractal, rising_segment_found])
    dim3_kline["positive_count"] = kline_positive_count
    dim3_kline["kline_positive"] = kline_positive_count >= 2

    # ── Dimension 4: Chip / cost distribution — skipped ─────────────────
    dim4_chip: dict[str, Any] | None = {
        "available": False,
        "note": "chip/cost-distribution data not available in bar-only input",
    }

    # ── Overall verdict ──────────────────────────────────────────────────
    verdict = dim1_pass and dim3_kline.get("kline_positive", False)

    base = 1.0 if dim1_pass else 0.0
    kline_boost = min(kline_positive_count * 0.12, 0.36)
    confidence = min(base + kline_boost, 1.0)

    if verdict:
        if has_bottom_divergence and rising_segment_found:
            signal = "reversal"
        elif has_fractal and not dim1_ma.get("bearish_arrangement", True):
            signal = "rebound"
        else:
            signal = "rebound"
    else:
        signal = "none"

    return {
        "signal": signal,
        "confidence": round(confidence, 3),
        "verdict": verdict,
        "checks": {
            "dim1_ma": dim1_ma,
            "dim2_fund": dim2_fund,
            "dim3_kline": dim3_kline,
            "dim4_chip": dim4_chip,
        },
    }
