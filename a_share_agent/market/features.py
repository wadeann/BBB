"""Pure deterministic market feature computation.

All functions are stateless and deterministic — same inputs always produce
same outputs. No MCP or I/O.
"""
from __future__ import annotations

from typing import Any, Sequence

# ── Feature version (bump on incompatible changes) ────────────────────
FEATURE_VERSION = "0.8.0"


# ── Breadth ────────────────────────────────────────────────────────────
# Input: list of per-stock snapshots, each with a "change_pct" field.
# Eligible = day's active trading stocks. IPO/delisted/suspended filtered
# out before calling these functions (caller's responsibility).


def compute_breadth(
    stocks: Sequence[dict[str, Any]],
    *,
    advance_threshold: float = 0.0,
    decline_threshold: float = 0.0,
    total_universe: int | None = None,
    filtered_out_ipo: int = 0,
    filtered_out_delisted: int = 0,
    filtered_out_suspended: int = 0,
) -> dict[str, Any]:
    """Advancing / declining counts and ratio.

    Parameters
    ----------
    stocks : sequence of dicts, each with 'change_pct' (float).
    advance_threshold, decline_threshold : change_pct must exceed (or be
        strictly below) these thresholds to count.  Default 0.0 means
        any positive → advancing, any negative → declining.
    total_universe : total universe before filtering (optional, for audit trail).
    filtered_out_ipo : count of symbols filtered for IPO not yet listed.
    filtered_out_delisted : count of symbols filtered for delisted.
    filtered_out_suspended : count of symbols filtered for suspended.

    Returns
    -------
    dict with advancing, declining, total, ratio, audit_trail
    ratio = advancing / (advancing + declining) when denominator > 0, else None.
    """
    advancing = 0
    declining = 0
    total = len(stocks)

    for s in stocks:
        chg = _safe_float(s, "change_pct")
        if chg is None:
            continue
        if chg > advance_threshold:
            advancing += 1
        elif chg < decline_threshold:
            declining += 1
    denom = advancing + declining
    ratio = round(advancing / denom, 4) if denom > 0 else None
    return {
        "advancing": advancing,
        "declining": declining,
        "total": total,
        "ratio": ratio,
        "eligible_count": total,
        "audit_trail": {
            "total_universe": total_universe if total_universe is not None else total,
            "filtered_out_ipo": filtered_out_ipo,
            "filtered_out_delisted": filtered_out_delisted,
            "filtered_out_suspended": filtered_out_suspended,
        },
    }


# ── Leaders ────────────────────────────────────────────────────────────
# Input: list of sector dicts with "sector_name" / "strength_score" and
# an optional "previous_top_sectors" (list[str]) for persistence tracking.


def compute_leaders(
    sectors: Sequence[dict[str, Any]],
    *,
    top_n: int = 5,
    previous_top: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Top N sectors by strength score with persistence count.

    Parameters
    ----------
    sectors : each dict has "sector_name" (str) and "strength_score" (float).
    top_n : how many sectors to return.
    previous_top : sector names that were in the top N on the previous day.

    Returns
    -------
    list of {"sector": str, "strength": float, "rank": int, "persistence": int}
    sorted descending by strength.
    """
    scored = []
    for s in sectors:
        name = s.get("sector_name", "")
        score = _safe_float(s, "strength_score")
        if not name or score is None:
            continue
        scored.append((name, score))

    scored.sort(key=lambda x: -x[1])
    prev_set = set(previous_top or [])

    result = []
    for i, (name, score) in enumerate(scored[:top_n]):
        persist = 1
        if name in prev_set:
            persist = 2  # caller tracks the full history externally
        result.append({
            "sector": name,
            "strength": round(score, 4),
            "rank": i + 1,
            "persistence": persist,
        })
    return result


# ── Turnover share ─────────────────────────────────────────────────────
# Input: list of sector dicts with "sector_name" and "turnover".


def compute_turnover_share(
    sectors: Sequence[dict[str, Any]],
    *,
    top_n: int = 3,
) -> float | None:
    """Top-N sectors turnover / total market turnover.

    Returns None when total turnover is zero or missing.
    """
    sector_turnovers: list[tuple[str, float]] = []
    total = 0.0
    for s in sectors:
        t = _safe_float(s, "turnover")
        name = s.get("sector_name", "")
        if t is None or not name:
            continue
        sector_turnovers.append((name, t))
        total += t

    if total <= 0:
        return None

    sector_turnovers.sort(key=lambda x: -x[1])
    top_sum = sum(t for _, t in sector_turnovers[:top_n])
    return round(top_sum / total, 4)


# ── Persistence ────────────────────────────────────────────────────────
# Count of consecutive days the same regime has been active.


def compute_persistence(
    current_regime: str,
    previous_regimes: str | list[str] | None = None,
) -> int:
    """Consecutive days with the same regime classification.

    Parameters
    ----------
    current_regime : today's regime string.
    previous_regimes : yesterday's regime (str) or list of recent regimes
                       ordered most-recent-first.  The count stops on the
                       first mismatch.

    Returns
    -------
    int >= 1 (current day always counts).
    """
    if isinstance(previous_regimes, str):
        return 2 if previous_regimes == current_regime else 1

    count = 1
    for r in (previous_regimes or []):
        if r == current_regime:
            count += 1
        else:
            break
    return count


# ── Expansion ──────────────────────────────────────────────────────────
# Number of sectors whose strength score exceeds a threshold.


def compute_expansion(
    sectors: Sequence[dict[str, Any]],
    *,
    threshold: float = 60.0,
) -> int:
    """Count of sectors with strength_score >= threshold."""
    count = 0
    for s in sectors:
        score = _safe_float(s, "strength_score")
        if score is not None and score >= threshold:
            count += 1
    return count


# ── Concentration (Herfindahl-Hirschman Index) ─────────────────────────
# Sum of squared market shares (turnover or cap weight).


def compute_concentration(
    sectors: Sequence[dict[str, Any]],
    *,
    value_key: str = "turnover",
) -> float | None:
    """Herfindahl-Hirschman style concentration index.

    HHI = sum((turnover_i / total_turnover) ** 2) for each sector.
    Values near 0 = fragmented; 1.0 = monopoly.
    Returns None when total is zero or no valid data.
    """
    values: list[float] = []
    for s in sectors:
        v = _safe_float(s, value_key)
        if v is not None and v > 0:
            values.append(v)

    total = sum(values)
    if total <= 0:
        return None

    hhi = sum((v / total) ** 2 for v in values)
    return round(hhi, 6)


# ── Helpers ────────────────────────────────────────────────────────────


def _safe_float(obj: dict[str, Any], key: str) -> float | None:
    try:
        v = obj.get(key)
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
