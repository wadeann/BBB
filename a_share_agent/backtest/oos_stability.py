"""OOS stability statistics for walk-forward validation.

Phase 2A gate: evaluates out-of-sample stability of a strategy across
multiple walk-forward folds. Produces classification: INSUFFICIENT_DATA,
UNSTABLE, or STABLE_CANDIDATE.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any

from .metrics import _quality_state


# ---------------------------------------------------------------------------
# Default thresholds (locked — non-OOS callers MUST NOT override)
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "min_observed_folds": 4,
    "min_informative_folds": 3,
    "min_closed_per_fold": 10,
    "min_total_closed": 40,
    "required_context_coverage": 1.0,
    "min_finite_pf_folds": 3,
    "min_median_expectancy": 0.0,  # strict >
    "min_median_pf": 1.0,          # strict >
    "min_positive_fold_fraction": 2 / 3,
    "max_drawdown_loss": 0.15,
    "min_worst_expectancy": -0.02,
    "max_positive_concentration": 0.5,
}


def _build_matrix(trades: list[dict], key: str) -> dict[str, dict]:
    """Group closed trades by a categorical field and return stats per group."""
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        k = str(t.get(key) or "UNKNOWN")
        groups[k].append(float(t["pnl_pct"]))
    out: dict[str, dict] = {}
    for k, returns in sorted(groups.items()):
        n = len(returns)
        winners = sum(1 for r in returns if r > 0)
        out[k] = {
            "trades": n,
            "total_return": sum(returns),
            "avg_return": mean(returns),
            "win_rate": winners / n if n else 0.0,
        }
    return out


# ---------------------------------------------------------------------------
# Fold-level summarization
# ---------------------------------------------------------------------------
def summarize_fold(report: dict) -> dict:
    """Summarize a single fold's trades into OOS stability statistics.

    Args:
        report: Fold report dict with:
            - fold_id: int
            - complete: bool
            - status: str
            - trades: list[dict] — flat trade list (direction,BUY|SELL,
              round_trip_id, pnl_pct fraction, exit_reason, trade_date,
              regime_at_signal, theme_lifecycle, pattern_id, pattern_version,
              regime_data_quality_at_signal, theme_data_quality_at_signal)
            - coverage: float

    Returns fold-level statistics dict.
    """
    fold_id = int(report.get("fold_id", 0))
    complete = bool(report.get("complete", True))
    status = str(report.get("status", "COMPLETED"))
    raw_cov = report.get("coverage", 1.0)
    coverage = float(
        raw_cov.get("tested_symbols", raw_cov) / max(raw_cov.get("requested_symbols", 1), 1)
    ) if isinstance(raw_cov, dict) else float(raw_cov)
    all_trades: list[dict] = list(report.get("trades", []) or [])

    # Categorise
    closed: list[dict] = []
    n_censored = 0
    buy_rids: set[str] = set()

    for t in all_trades:
        if t.get("direction") == "BUY":
            rid = str(t.get("round_trip_id") or "")
            if rid:
                buy_rids.add(rid)

    closed_rids: set[str] = set()
    for t in all_trades:
        if t.get("direction") != "SELL":
            continue
        pnl = t.get("pnl_pct")
        if pnl is None:
            raise ValueError(
                f"Trade {t.get('round_trip_id')} in fold {fold_id} "
                "has None pnl_pct"
            )
        if t.get("exit_reason") == "END_OF_BACKTEST":
            n_censored += 1
            continue
        rid = str(t.get("round_trip_id") or "")
        if rid:
            closed_rids.add(rid)
        closed.append(t)

    n_closed = len(closed)
    n_open = len(buy_rids - closed_rids)
    # ---- Detect duplicate SELL round_trip_ids ----
    if len(closed_rids) != n_closed:
        raise ValueError(
            f"Fold {fold_id} has duplicate SELL round_trip_ids: "
            f"{n_closed} closed trades but only {len(closed_rids)} unique IDs"
        )

    # Context quality via _quality_state (reuses entry_context_quality)
    if closed:
        quality_states = [_quality_state(t) for t in closed]
        q_ok = quality_states.count("ok")
        context_eligible = q_ok == n_closed
        quality_reason = "ALL_OK" if context_eligible else "DEGRADED"
    else:
        context_eligible = True
        quality_reason = "NO_TRADES"

    if n_closed == 0:
        return {
            "fold_id": fold_id, "complete": complete, "status": status,
            "n_closed": 0, "n_open": n_open, "n_censored": n_censored,
            "closed_round_trip_ids": [],
            "closed_returns": [],
            "expectancy_pct": None, "median_trade_pct": None,
            "profit_factor": None, "pf_reason": "NO_TRADES",
            "n_winners": 0, "n_losers": 0, "win_rate": None,
            "max_drawdown_pct": None, "avg_mfe_pct": None, "avg_mae_pct": None,
            "fold_total_return_pct": None,
            "regime_matrix": {}, "theme_matrix": {}, "pattern_matrix": {},
            "version_matrix": {},
            "context_eligible": context_eligible, "quality_reason": quality_reason,
            "coverage": coverage,
        }
    closed_rtids = sorted(closed_rids)
    returns_list = [float(t["pnl_pct"]) for t in closed]
    winners = [r for r in returns_list if r > 0]
    losers = [r for r in returns_list if r < 0]
    n_winners = len(winners)
    n_losers = len(losers)
    win_rate = n_winners / n_closed
    expectancy = mean(returns_list)
    median_return = median(returns_list)

    sum_positive = sum(winners)
    sum_negative_abs = abs(sum(losers))
    if n_losers == 0:
        profit_factor = None
        pf_reason = "NO_LOSSES"
    elif n_winners == 0:
        profit_factor = 0.0
        pf_reason = "FINITE"
    else:
        profit_factor = sum_positive / sum_negative_abs
        pf_reason = "FINITE"

    fold_total_return = sum(returns_list)

    # Drawdown
    sorted_trades = sorted(
        closed,
        key=lambda x: (
            str(x.get("trade_date", "")),
            str(x.get("round_trip_id", "")),
        ),
    )
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for t in sorted_trades:
        equity *= 1.0 + float(t["pnl_pct"])
        if equity > peak:
            peak = equity
        dd = equity / peak - 1.0
        if dd < max_drawdown:
            max_drawdown = dd

    mfe_vals = [float(t["mfe_pct"]) for t in closed if t.get("mfe_pct") is not None]
    mae_vals = [float(t["mae_pct"]) for t in closed if t.get("mae_pct") is not None]
    avg_mfe = mean(mfe_vals) if mfe_vals else None
    avg_mae = mean(mae_vals) if mae_vals else None

    regime_matrix = _build_matrix(closed, "regime_at_signal")
    theme_matrix = _build_matrix(closed, "theme_lifecycle")
    pattern_matrix = _build_matrix(closed, "pattern_id")
    version_matrix = _build_matrix(closed, "pattern_version")

    result = {
        "fold_id": fold_id,
        "complete": complete,
        "status": status,
        "n_closed": n_closed,
        "n_open": n_open,
        "n_censored": n_censored,
        "closed_round_trip_ids": closed_rtids,
        "closed_returns": returns_list,
        "expectancy_pct": expectancy, "median_trade_pct": median_return,
        "profit_factor": profit_factor, "pf_reason": pf_reason,
        "n_winners": n_winners, "n_losers": n_losers, "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown,
        "avg_mfe_pct": avg_mfe, "avg_mae_pct": avg_mae,
        "fold_total_return_pct": fold_total_return,
        "regime_matrix": regime_matrix, "theme_matrix": theme_matrix,
        "pattern_matrix": pattern_matrix, "version_matrix": version_matrix,
        "context_eligible": context_eligible, "quality_reason": quality_reason,
        "coverage": coverage,
    }
    return result


# ---------------------------------------------------------------------------
# Aggregate stability across folds
# ---------------------------------------------------------------------------
def aggregate_stability(
    summaries: list[dict],
    thresholds: dict | None = None,
) -> dict:
    """Aggregate fold summaries through OOS stability gates.

    Args:
        summaries: per-fold summaries from summarize_fold.
        thresholds: optional override; defaults locked.

    Returns aggregate stability verdict with evidence.
    """
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds is not None:
        t.update(thresholds)

    n_observed = len(summaries)

    informative: list[dict] = []
    failed: list[dict] = []
    for s in summaries:
        if s.get("complete") and s.get("status") == "COMPLETED":
            if s["n_closed"] >= t["min_closed_per_fold"] and s.get("context_eligible"):
                informative.append(s)
        else:
            failed.append(s)

    n_informative = len(informative)
    n_failed = len(failed)

    # Collect aggregates from all completed folds
    total_closed = 0
    all_pooled_returns: list[float] = []
    fold_medians: list[float] = []
    fold_expectancies: list[float] = []
    fold_pf_values: list[float] = []
    fold_drawdowns: list[float] = []
    fold_total_returns: list[float] = []

    for s in summaries:
        total_closed += s["n_closed"]
        if s["n_closed"] > 0 and s.get("status") == "COMPLETED":
            all_pooled_returns.extend(s.get("closed_returns") or [])
            fold_medians.append(s["median_trade_pct"])
            fold_expectancies.append(s["expectancy_pct"])
            if s["profit_factor"] is not None:
                fold_pf_values.append(s["profit_factor"])
            if s["fold_total_return_pct"] is not None:
                fold_total_returns.append(s["fold_total_return_pct"])

    pooled_median = median(all_pooled_returns) if all_pooled_returns else None
    fold_median_trade = median(fold_medians) if fold_medians else None
    fold_median_expectancy = median(fold_expectancies) if fold_expectancies else None
    fold_median_pf = median(fold_pf_values) if fold_pf_values else None
    positive_fold_frac = (
        sum(1 for e in fold_expectancies if e > 0) / len(fold_expectancies)
        if fold_expectancies else 0.0
    )
    worst_fold_expectancy = min(fold_expectancies) if fold_expectancies else None
    max_fold_dd = min(fold_drawdowns) if fold_drawdowns else None
    total_returns_sum = sum(fold_total_returns) if fold_total_returns else 0.0
    max_concentration = (
        max(fold_total_returns) / total_returns_sum
        if fold_total_returns and total_returns_sum > 0
        else None
    )
    # ---- Cross-fold duplicate round_trip_id detection ----
    all_rids: list[str] = []
    cross_fold_dup = []
    for s in summaries:
        all_rids.extend(s.get("closed_round_trip_ids") or [])
    if all_rids:
        from collections import Counter
        rid_counts = Counter(all_rids)
        cross_fold_dup = [rid for rid, cnt in rid_counts.items() if cnt > 1]

    # ---- Gates ----
    sample_size_reason = None
    if n_observed < t["min_observed_folds"]:
        sample_size_reason = f"observed_folds={n_observed} < {t['min_observed_folds']}"
    elif n_informative < t["min_informative_folds"]:
        sample_size_reason = f"informative_folds={n_informative} < {t['min_informative_folds']}"
    elif total_closed < t["min_total_closed"]:
        sample_size_reason = f"total_closed={total_closed} < {t['min_total_closed']}"

    coverage_reason = None
    for s in summaries:
        if s.get("coverage", 1.0) < t["required_context_coverage"]:
            coverage_reason = f"fold {s['fold_id']} coverage={s['coverage']} < {t['required_context_coverage']}"
            break

    missing_return_reason = None
    pf_reason = None
    if len(fold_pf_values) < t["min_finite_pf_folds"]:
        pf_reason = f"finite_pf_folds={len(fold_pf_values)} < {t['min_finite_pf_folds']}"
    if cross_fold_dup:
        raise ValueError(
            f"duplicate round_trip_ids across folds: "
            f"{sorted(cross_fold_dup)[:5]}"
        )

    dup_reason = None
    # Classification
    if sample_size_reason or coverage_reason or missing_return_reason or pf_reason or dup_reason:
        classification = "INSUFFICIENT_DATA"
    else:
        economic_reasons = []
        if fold_median_expectancy is not None and fold_median_expectancy <= t["min_median_expectancy"]:
            economic_reasons.append(f"median_expectancy={fold_median_expectancy:.6f} <= {t['min_median_expectancy']}")
        if fold_median_pf is not None and fold_median_pf <= t["min_median_pf"]:
            economic_reasons.append(f"median_pf={fold_median_pf:.6f} <= {t['min_median_pf']}")
        if positive_fold_frac < t["min_positive_fold_fraction"]:
            economic_reasons.append(f"positive_fold_fraction={positive_fold_frac:.6f} < {t['min_positive_fold_fraction']}")
        if max_fold_dd is not None and max_fold_dd <= -t["max_drawdown_loss"]:
            economic_reasons.append(f"max_fold_drawdown={max_fold_dd:.6f} <= -{t['max_drawdown_loss']}")
        if worst_fold_expectancy is not None and worst_fold_expectancy < t["min_worst_expectancy"]:
            economic_reasons.append(f"worst_fold_expectancy={worst_fold_expectancy:.6f} < {t['min_worst_expectancy']}")
        if max_concentration is not None and max_concentration > t["max_positive_concentration"]:
            economic_reasons.append(f"max_concentration={max_concentration:.6f} > {t['max_positive_concentration']}")
        classification = "UNSTABLE" if economic_reasons else "STABLE_CANDIDATE"

    # Individual reasons
    drawdown_reason = (
        f"max_fold_drawdown={max_fold_dd:.6f} <= -{t['max_drawdown_loss']}"
        if max_fold_dd is not None and max_fold_dd <= -t["max_drawdown_loss"]
        else None
    )
    concentration_reason = (
        f"max_concentration={max_concentration:.6f} > {t['max_positive_concentration']}"
        if max_concentration is not None and max_concentration > t["max_positive_concentration"]
        else None
    )
    expectancy_reason = (
        f"fold_median_expectancy={fold_median_expectancy:.6f} <= {t['min_median_expectancy']}"
        if fold_median_expectancy is not None and fold_median_expectancy <= t["min_median_expectancy"]
        else None
    )

    result: dict[str, Any] = {
        "classification": classification,
        "n_observed_folds": n_observed,
        "n_informative_folds": n_informative,
        "n_failed_folds": n_failed,
        "n_total_closed": total_closed,
        "pooled_median_trade_pct": pooled_median,
        "fold_median_trade_pct": fold_median_trade,
        "fold_median_expectancy_pct": fold_median_expectancy,
        "fold_median_profit_factor": fold_median_pf,
        "max_pooled_drawdown_pct": max_fold_dd,
        "max_fold_drawdown_pct": max_fold_dd,
        "positive_fold_fraction": positive_fold_frac,
        "worst_fold_expectancy_pct": worst_fold_expectancy,
        "max_fold_return_concentration": max_concentration,
        "sample_size_reason": sample_size_reason,
        "coverage_reason": coverage_reason,
        "missing_return_reason": missing_return_reason,
        "pf_reason": pf_reason,
        "duplicate_id_reason": dup_reason,
        "expectancy_reason": expectancy_reason,
        "drawdown_reason": drawdown_reason,
        "concentration_reason": concentration_reason,
    }

    _json_safe(result)
    return result


def _json_safe(d: dict) -> None:
    """Replace non-finite floats with None in-place."""
    import math
    for k, v in list(d.items()):
        if isinstance(v, float) and not math.isfinite(v):
            d[k] = None
        elif isinstance(v, dict):
            _json_safe(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _json_safe(item)
