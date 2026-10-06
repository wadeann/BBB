"""6-dimension v4 scoring card — deterministic, reproducible, total 100 points.

Each dimension produces a sub-score from its maximum.  The overall score is the
sum of all six sub-scores, clamped to [0, 100].  Scoring functions depend *only*
on supplied bar data and metadata — no LLM calls, no random state, no network
dependencies.

Dimension breakdown
-------------------
1. Supply-demand / Catalyst     20 pts  — gap clarity, catalyst not priced in
2. Sector Resonance             20 pts  — mainline sector, echelon, money flow
3. Trend Structure              20 pts  — MA5 > MA10 > MA20, multi-level resonance
4. Money Flow                   15 pts  — net institutional inflow, profit-taking
5. Volume-Price Pattern         15 pts  — 2-3× volume expansion, shrinking pullback
6. News / Fundamentals          10 pts  — no negative news, earnings delivery
"""

from __future__ import annotations

from statistics import mean
from typing import Any

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SCORING_SCHEMA_VERSION = "1.0.0"


def score_all(
    bars: list[dict[str, Any]],
    *,
    sector_rank: float = 0.0,
    sector_mainline: bool = False,
    echelon_count: int = 0,
    institutional_net: float = 0.0,
    profit_taking_pct: float = 0.0,
    news_negative: bool = False,
    earnings_delivered: bool = True,
) -> dict[str, Any]:
    """Compute a full 6-dimension v4 scoring card.

    Parameters
    ----------
    bars : list[dict]
        OHLCV bar list, most recent bar last.  At least 25 entries required.
    sector_rank : float
        Sector strength rank percentile [0, 1], 1 = strongest.
    sector_mainline : bool
        Whether the sector is a current mainline lane.
    echelon_count : int
        Number of same-sector peers with strong signals (completed echelon).
    institutional_net : float
        Net institutional money flow reading (positive = inflow).
    profit_taking_pct : float
        Percentage of holders in profit [0, 100].
    news_negative : bool
        Whether negative news is present for the symbol.
    earnings_delivered : bool
        Whether earnings/forecast met or exceeded expectations.

    Returns
    -------
    dict with keys:
        score          : float  — [0, 100]
        breakdown      : dict[str, float] — {dimension_name: sub_score}
        reasons        : dict[str, list[str]] — evidence per dimension
        schema_version : str
    """
    if not bars or len(bars) < 25:
        return {
            "score": 0.0,
            "breakdown": {},
            "reasons": {"global": ["insufficient_bar_data"]},
            "schema_version": SCORING_SCHEMA_VERSION,
        }

    d1 = _score_supply_demand_catalyst(bars)
    d2 = _score_sector_resonance(sector_rank, sector_mainline, echelon_count)
    d3 = _score_trend_structure(bars)
    d4 = _score_money_flow(institutional_net, profit_taking_pct)
    d5 = _score_volume_price_pattern(bars)
    d6 = _score_news_fundamentals(news_negative, earnings_delivered)

    breakdown = {
        "supply_demand_catalyst": round(d1["score"], 1),
        "sector_resonance": round(d2["score"], 1),
        "trend_structure": round(d3["score"], 1),
        "money_flow": round(d4["score"], 1),
        "volume_price_pattern": round(d5["score"], 1),
        "news_fundamentals": round(d6["score"], 1),
    }
    total = sum(breakdown.values())
    total = max(0.0, min(100.0, total))

    return {
        "score": round(total, 1),
        "breakdown": breakdown,
        "reasons": {
            "supply_demand_catalyst": d1["reasons"],
            "sector_resonance": d2["reasons"],
            "trend_structure": d3["reasons"],
            "money_flow": d4["reasons"],
            "volume_price_pattern": d5["reasons"],
            "news_fundamentals": d6["reasons"],
        },
        "schema_version": SCORING_SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Dimension helpers
# ---------------------------------------------------------------------------

def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract(bars: list[dict[str, Any]]) -> dict[str, list[float]]:
    c = [_f(x.get("close")) for x in bars]
    h = [_f(x.get("high")) for x in bars]
    l = [_f(x.get("low")) for x in bars]
    o = [_f(x.get("open")) for x in bars]
    v = [_f(x.get("volume")) for x in bars]
    return {"close": c, "high": h, "low": l, "open": o, "volume": v}


def _sma(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    acc = 0.0
    for i, val in enumerate(vals):
        acc += val
        if i >= n:
            acc -= vals[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


# ── D1: Supply-demand / Catalyst (20 pts) ─────────────────────────────

def _score_supply_demand_catalyst(bars: list[dict[str, Any]]) -> dict:
    """Score gap clarity and catalyst-not-priced-in from bar price action.

    Up to 20 pts:
      - 10 pts: recent gap-up (closing > 2% above prior close)
      -  5 pts: gap sustained for 5+ bars (no full gap closure)
      -  5 pts: recent low retracement < 30% of gap move (catalyst not exhausted)
    """
    reasons: list[str] = []
    score = 0.0
    c = _extract(bars)["close"]
    h = _extract(bars)["high"]
    l = _extract(bars)["low"]
    i = len(c) - 1

    if i < 5:
        reasons.append("insufficient_bars")
        return {"score": 0.0, "reasons": reasons}

    # Find largest gap-up in last 10 bars
    gap_pct = 0.0
    gap_idx = -1
    for j in range(max(1, i - 9), i + 1):
        if c[j - 1] > 0:
            jump = (c[j] - c[j - 1]) / c[j - 1]
            if jump > gap_pct:
                gap_pct = jump
                gap_idx = j

    if gap_pct >= 0.02 and gap_idx >= 1:
        score += 10.0
        reasons.append(f"recent_gap_up_{round(gap_pct * 100, 1)}pct")

        # Gap sustained: no full closure in subsequent bars
        gap_open = _f(bars[gap_idx].get("open"))
        sustained = True
        for j in range(gap_idx + 1, len(c)):
            if h[j] is not None and gap_open > 0 and l[j] <= gap_open:
                sustained = False
                break
        if sustained:
            score += 5.0
            reasons.append("gap_sustained")
        else:
            reasons.append("gap_closed_partially")

        # Catalyst not exhausted: retracement < 30% of gap move
        gap_high = max(h[gap_idx], c[gap_idx]) if h else c[gap_idx]
        gap_low = min(l[gap_idx], c[gap_idx])
        gap_range = gap_high - gap_low
        post_low = min(x for x in l[gap_idx:] if x is not None)
        if gap_range > 0:
            retrace_pct = (gap_high - post_low) / gap_range
            if retrace_pct < 0.3:
                score += 5.0
                reasons.append(f"retracement_{round(retrace_pct * 100, 0)}pct_of_gap")
            else:
                reasons.append(f"retracement_exhausted_{round(retrace_pct * 100, 0)}pct")
        else:
            reasons.append("flat_gap_no_range")
    else:
        reasons.append("no_significant_gap_up")

    return {"score": min(score, 20.0), "reasons": reasons}


# ── D2: Sector Resonance (20 pts) ─────────────────────────────────────

def _score_sector_resonance(
    sector_rank: float,
    sector_mainline: bool,
    echelon_count: int,
) -> dict:
    """Score sector alignment.

    Up to 20 pts:
      - 10 pts: sector is mainline (rank > 0.7 percentile or explicit mainline)
      -  5 pts: complete echelon (3+ strong same-sector peers)
      -  5 pts: positive sector money flow implied by rank + echelon
    """
    reasons: list[str] = []
    score = 0.0

    if sector_rank >= 0.7 or sector_mainline:
        score += 10.0
        reasons.append("mainline_sector")
    elif sector_rank >= 0.4:
        score += 5.0
        reasons.append("neutral_sector")
    else:
        reasons.append("weak_sector")

    if echelon_count >= 3:
        score += 5.0
        reasons.append(f"complete_echelon_{echelon_count}_peers")
    elif echelon_count >= 1:
        score += 2.0
        reasons.append(f"partial_echelon_{echelon_count}_peers")
    else:
        reasons.append("no_echelon_peers")

    # Positive money flow: resonance from rank + echelon
    if sector_rank >= 0.5 and echelon_count >= 1:
        score += 5.0
        reasons.append("positive_sector_money_flow")

    return {"score": min(score, 20.0), "reasons": reasons}


# ── D3: Trend Structure (20 pts) ──────────────────────────────────────

def _score_trend_structure(bars: list[dict[str, Any]]) -> dict:
    """Score multi-level moving-average alignment.

    Up to 20 pts:
      -  8 pts: MA5 > MA10 > MA20 (perfect alignment)
      -  6 pts: price above all three MAs
      -  6 pts: multi-level resonance (MA slopes positive)
    """
    reasons: list[str] = []
    score = 0.0
    c = _extract(bars)["close"]
    i = len(c) - 1
    if i < 25:
        reasons.append("insufficient_bars")
        return {"score": 0.0, "reasons": reasons}

    ma5 = _sma(c, 5)
    ma10 = _sma(c, 10)
    ma20 = _sma(c, 20)

    if None in (ma5[i], ma10[i], ma20[i]):
        reasons.append("ma_unavailable")
        return {"score": 0.0, "reasons": reasons}

    m5, m10, m20 = float(ma5[i]), float(ma10[i]), float(ma20[i])

    # Perfect alignment MA5 > MA10 > MA20
    if m5 > m10 > m20:
        score += 8.0
        reasons.append(f"ma_aligned_{round(m5,2)}>{round(m10,2)}>{round(m20,2)}")
    elif m5 > m10 or m5 > m20:
        score += 4.0
        reasons.append("partial_ma_alignment")
    else:
        reasons.append("no_ma_alignment")

    # Price above all three MAs
    close = c[i]
    if close > m5 and close > m10 and close > m20:
        score += 6.0
        reasons.append("price_above_all_mas")
    elif close > m5:
        score += 3.0
        reasons.append("price_above_ma5_only")

    # Multi-level resonance: MA5 slope + MA10 slope + MA20 slope positive
    positive_slopes = 0
    for ma_vals, name in [(ma5, "ma5"), (ma10, "ma10"), (ma20, "ma20")]:
        if i >= 3 and ma_vals[i] is not None and ma_vals[i - 3] is not None:
            if float(ma_vals[i]) > float(ma_vals[i - 3]):
                positive_slopes += 1
    if positive_slopes >= 2:
        score += 6.0
        reasons.append(f"multi_level_resonance_{positive_slopes}_positive_slopes")
    elif positive_slopes >= 1:
        score += 3.0
        reasons.append(f"partial_resonance_{positive_slopes}_slope")

    return {"score": min(score, 20.0), "reasons": reasons}


# ── D4: Money Flow (15 pts) ───────────────────────────────────────────

def _score_money_flow(institutional_net: float, profit_taking_pct: float) -> dict:
    """Score institutional flow and profit-taking level.

    Up to 15 pts:
      - 10 pts: net institutional inflow positive
      -  5 pts: profit-taking > 50% (shows price momentum)
    """
    reasons: list[str] = []
    score = 0.0

    if institutional_net > 0:
        score += 10.0
        reasons.append(f"net_institutional_inflow_{round(institutional_net, 2)}")
    else:
        reasons.append(f"net_institutional_outflow_{round(institutional_net, 2)}")

    if profit_taking_pct > 50:
        score += 5.0
        reasons.append(f"profit_taking_{round(profit_taking_pct, 1)}pct")
    else:
        reasons.append(f"low_profit_taking_{round(profit_taking_pct, 1)}pct")

    return {"score": min(score, 15.0), "reasons": reasons}


# ── D5: Volume-Price Pattern (15 pts) ─────────────────────────────────

def _score_volume_price_pattern(bars: list[dict[str, Any]]) -> dict:
    """Score volume expansion and pullback contraction.

    Up to 15 pts:
      -  8 pts: 2-3× volume expansion on recent up bars
      -  7 pts: shrinking pullback volume (lower volume on red days after move)
    """
    reasons: list[str] = []
    score = 0.0
    d = _extract(bars)
    c, v, h = d["close"], d["volume"], d["high"]
    i = len(c) - 1

    if i < 20:
        reasons.append("insufficient_bars")
        return {"score": 0.0, "reasons": reasons}

    vv20 = _sma(v, 20)

    if vv20[i] is None or float(vv20[i]) <= 0:
        reasons.append("volume_ma_unavailable")
        return {"score": 0.0, "reasons": reasons}

    v20_avg = float(vv20[i])

    # Look for up-bars in last 10 with 2-3× volume
    volume_expansion_found = False
    expansion_bars = 0
    for j in range(max(1, i - 9), i + 1):
        if c[j] > c[j - 1] and v[j] >= 2 * v20_avg and v[j] <= 3 * v20_avg:
            volume_expansion_found = True
            expansion_bars += 1

    if volume_expansion_found:
        score += 8.0
        reasons.append(f"volume_expansion_{expansion_bars}_bars_2_3x")
    else:
        # Partial credit for any volume expansion
        for j in range(max(1, i - 9), i + 1):
            if c[j] > c[j - 1] and v[j] > v20_avg:
                score += 4.0
                reasons.append("moderate_volume_expansion")
                break
        else:
            reasons.append("no_volume_expansion")

    # Shrinking pullback: volume on red days after the move declines
    red_volumes = [v[j] for j in range(max(1, i - 9), i + 1) if c[j] < c[j - 1]]
    if red_volumes and len(red_volumes) >= 2:
        avg_first_half = mean(red_volumes[: len(red_volumes) // 2])
        avg_second_half = mean(red_volumes[len(red_volumes) // 2 :])
        if avg_second_half < avg_first_half and avg_first_half > 0:
            score += 7.0
            reasons.append(f"shrinking_pullback_volume_{round(avg_second_half/avg_first_half, 2)}x")
        else:
            score += 3.0
            reasons.append("stable_pullback_volume")
    elif red_volumes:
        score += 3.0
        reasons.append("minimal_pullback_data")
    else:
        reasons.append("no_pullback_bars")

    return {"score": min(score, 15.0), "reasons": reasons}


# ── D6: News / Fundamentals (10 pts) ──────────────────────────────────

def _score_news_fundamentals(news_negative: bool, earnings_delivered: bool) -> dict:
    """Score news and fundamental quality.

    Up to 10 pts:
      -  5 pts: no negative news
      -  5 pts: earnings delivered
    """
    reasons: list[str] = []
    score = 0.0

    if not news_negative:
        score += 5.0
        reasons.append("no_negative_news")
    else:
        reasons.append("negative_news_present")

    if earnings_delivered:
        score += 5.0
        reasons.append("earnings_delivered")
    else:
        reasons.append("earnings_missed")

    return {"score": min(score, 10.0), "reasons": reasons}
