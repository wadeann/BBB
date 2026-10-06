"""OOS stability statistics for walk-forward validation.

Phase 2A gate: evaluates out-of-sample stability of a strategy across
multiple walk-forward folds. Produces classification: INSUFFICIENT_DATA,
UNSTABLE, or STABLE_CANDIDATE.

Phase 2B extension: per-four-key OOS stability for exact key gating.
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
    fold_id = report.get("fold_id", 0)
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
    buys_by_id: dict[str, list[dict]] = defaultdict(list)
    attribution_valid = True

    for t in all_trades:
        if t.get("direction") == "BUY":
            rid = str(t.get("round_trip_id") or "")
            if rid:
                buy_rids.add(rid)
                buys_by_id[rid].append(t)
            else:
                attribution_valid = False

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
        rid = str(t.get("round_trip_id") or "")
        buys = buys_by_id.get(rid, [])
        if (not rid or len(buys) != 1
                or _four_key_from_trade(buys[0]) != _four_key_from_trade(t)):
            attribution_valid = False
            continue
        if t.get("exit_reason") == "END_OF_BACKTEST":
            n_censored += 1
            continue
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
    if any(len(buys) != 1 for buys in buys_by_id.values()):
        attribution_valid = False

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
            "attribution_valid": attribution_valid,
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
        "attribution_valid": attribution_valid,
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
    fold_positive_returns: list[float] = []

    for s in summaries:
        total_closed += s["n_closed"]
        if s["n_closed"] > 0 and s.get("status") == "COMPLETED":
            all_pooled_returns.extend(s.get("closed_returns") or [])
            fold_medians.append(s["median_trade_pct"])
            fold_expectancies.append(s["expectancy_pct"])
            if s["profit_factor"] is not None:
                fold_pf_values.append(s["profit_factor"])
            if s.get("max_drawdown_pct") is not None:
                fold_drawdowns.append(s["max_drawdown_pct"])
            fold_positive_returns.append(sum(max(float(r), 0.0) for r in s.get("closed_returns", [])))

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
    total_positive = sum(fold_positive_returns)
    max_concentration = (
        max(fold_positive_returns) / total_positive
        if total_positive > 0 else None
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
    integrity_reason = None
    fold_ids = [str(s.get("fold_id", "")) for s in summaries]
    if n_failed:
        integrity_reason = "failed or incomplete folds"
    elif len(set(fold_ids)) != len(fold_ids):
        integrity_reason = "duplicate fold_id in summaries"
    elif any(not s.get("attribution_valid", True) for s in summaries):
        integrity_reason = "invalid trade attribution"
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
        dup_reason = f"duplicate round_trip_ids across folds: {sorted(cross_fold_dup)[:5]}"
    else:
        dup_reason = None
    # Classification
    if integrity_reason or sample_size_reason or coverage_reason or missing_return_reason or pf_reason or dup_reason:
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
        "NO_POSITIVE_RETURNS" if total_positive == 0 else
        f"max_concentration={max_concentration:.6f} > {t['max_positive_concentration']}"
        if max_concentration is not None and max_concentration > t["max_positive_concentration"]
        else None
    )
    worst_expectancy_reason = (
        f"worst_fold_expectancy={worst_fold_expectancy:.6f} < {t['min_worst_expectancy']}"
        if worst_fold_expectancy is not None and worst_fold_expectancy < t["min_worst_expectancy"]
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
        "integrity_reason": integrity_reason,
        "worst_expectancy_reason": worst_expectancy_reason,
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


# ---------------------------------------------------------------------------
# Four-key constants
# ---------------------------------------------------------------------------
FOUR_KEY_FIELDS: tuple[str, ...] = (
    "regime_at_signal",
    "theme_lifecycle",
    "pattern_id",
    "pattern_version",
)

DEFAULT_PER_KEY_SCHEMA_VERSION: str = "1.0"


def _four_key_from_trade(trade: dict) -> tuple[str, str, str, str]:
    """Extract the canonical four-key tuple from a trade dict.

    Returns (regime_at_signal, theme_lifecycle, pattern_id, pattern_version).
    None/missing fields default to "UNKNOWN".
    """
    return (
        str(trade.get("regime_at_signal") or "UNKNOWN"),
        str(trade.get("theme_lifecycle") or "UNKNOWN"),
        str(trade.get("pattern_id") or "UNKNOWN"),
        str(trade.get("pattern_version") or "UNKNOWN"),
    )


def _key_to_artifact_key(key: tuple[str, str, str, str]) -> str:
    """Encode a four-key tuple as a collision-free artifact key string.

    Uses URI percent-encoding on each component to ensure '::' never
    appears inside an encoded component, preventing delimiter collisions.
    """
    from urllib.parse import quote
    return "::".join(quote(str(c), safe='') for c in key)


def _artifact_key_to_tuple(artifact_key: str) -> tuple[str, str, str, str]:
    """Decode a percent-encoded artifact key back to a four-key tuple."""
    from urllib.parse import unquote
    parts = artifact_key.split("::", 3)
    decoded = tuple(unquote(p) for p in parts)
    if len(decoded) < 4:
        decoded = decoded + ("UNKNOWN",) * (4 - len(decoded))
    return decoded  # type: ignore[return-value]


# Attribution audit helper
# ---------------------------------------------------------------------------
def _validate_full_cohort(fold_reports: list[dict],
                           expected_fold_ids: list[int | str] | None = None
                           ) -> dict[str, Any]:
    """Validate the full raw cohort before per-key grouping.

    Checks:
      - Complete expected fold plan (all IDs present, non-duplicate, COMPLETED)
      - Global round_trip_id uniqueness across all folds
      - Every SELL has a matching BUY with the same round_trip_id
      - No None or non-finite pnl_pct
      - All trades have trustworthy entry context (quality state='ok')

    Raises ValueError on first failure with diagnostic info.
    Returns dict with 'valid': True or raises.
    """
    # 1. Expected fold plan
    if expected_fold_ids is not None:
        expected_list = [str(fid) for fid in expected_fold_ids]
        if len(set(expected_list)) != len(expected_list):
            raise ValueError("duplicate fold_id in expected plan")
        expected_set = set(expected_list)
        seen_ids: set[str] = set()
        for report in fold_reports:
            fid = str(report.get("fold_id", ""))
            if fid in seen_ids:
                raise ValueError(f"duplicate fold_id in reports: {fid}")
            seen_ids.add(fid)
        missing = expected_set - seen_ids
        if missing:
            raise ValueError(f"missing expected fold IDs: {sorted(missing)}")
        extra = seen_ids - expected_set
        if extra:
            raise ValueError(f"unexpected fold IDs not in plan: {sorted(extra)}")
        for report in fold_reports:
            fid = str(report.get("fold_id", ""))
            if not report.get("complete", True) or report.get("status", "COMPLETED") != "COMPLETED":
                raise ValueError(f"fold {fid} failed or incomplete")
    all_rids: dict[str, list[tuple[str, str]]] = {}
    for report in fold_reports:
        fid = str(report.get("fold_id", ""))
        for trade in (report.get("trades") or []):
            direction = trade.get("direction")
            rid_value = trade.get("round_trip_id")
            if direction not in ("BUY", "SELL"):
                raise ValueError(f"fold {fid} has invalid trade direction {direction!r}")
            if rid_value is None or not str(rid_value).strip():
                raise ValueError(f"fold {fid} has missing round_trip_id")
            rid = str(rid_value)
            all_rids.setdefault(rid, []).append((fid, direction))
    dup_rids = [
        (rid, entries) for rid, entries in all_rids.items()
        if len({fold_id for fold_id, _ in entries}) > 1
    ]
    if dup_rids:
        raise ValueError(f"duplicate round_trip_id(s) across folds: {dup_rids[:5]}")

    per_fold_rids: dict[str, dict[str, list[str]]] = {}
    for report in fold_reports:
        fid = str(report.get("fold_id", ""))
        rid_dir: dict[str, list[str]] = {}
        for trade in (report.get("trades") or []):
            rid_dir.setdefault(str(trade["round_trip_id"]), []).append(trade["direction"])
        per_fold_rids[fid] = rid_dir
    for fid, rid_dir in per_fold_rids.items():
        for rid, dirs in rid_dir.items():
            if dirs.count("BUY") > 1 or dirs.count("SELL") > 1:
                raise ValueError(f"fold {fid} has duplicate trade attribution for {rid}")
            if "SELL" in dirs and "BUY" not in dirs:
                raise ValueError(f"fold {fid} round_trip_id '{rid}' missing matching BUY")
            if "BUY" in dirs and "SELL" in dirs:
                buy = next(t for t in next(r for r in fold_reports if str(r.get("fold_id", "")) == fid)["trades"] if str(t["round_trip_id"]) == rid and t["direction"] == "BUY")
                sell = next(t for t in next(r for r in fold_reports if str(r.get("fold_id", "")) == fid)["trades"] if str(t["round_trip_id"]) == rid and t["direction"] == "SELL")
                for field in ("regime_at_signal", "theme_lifecycle", "pattern_id", "pattern_version"):
                    if buy.get(field) != sell.get(field):
                        raise ValueError(f"fold {fid} round_trip_id '{rid}' mismatched {field}")

    import math
    for report in fold_reports:
        fid = str(report.get("fold_id", ""))
        for trade in (report.get("trades") or []):
            if trade.get("direction") == "SELL":
                try:
                    pnl = float(trade.get("pnl_pct"))
                except (TypeError, ValueError):
                    raise ValueError(f"fold {fid} SELL round_trip_id '{trade.get('round_trip_id')}' has invalid pnl_pct")
                if not math.isfinite(pnl):
                    raise ValueError(f"fold {fid} SELL round_trip_id '{trade.get('round_trip_id')}' non-finite pnl_pct")
            for qf in ("regime_data_quality_at_signal", "theme_data_quality_at_signal"):
                q = trade.get(qf)
                if not isinstance(q, dict) or q.get("state") != "ok":
                    raise ValueError(f"fold {fid} trade {trade.get('round_trip_id')} has missing/degraded {qf}")
    return {"valid": True}


# ---------------------------------------------------------------------------
# Per-four-key OOS stability
# ---------------------------------------------------------------------------
def per_key_oos_stability(
    fold_reports: list[dict],
    thresholds: dict | None = None,
    declared_keys: list[tuple[str, str, str, str]] | None = None,
    expected_fold_ids: list[int | str] | None = None,
) -> dict:
    """Compute per-four-key OOS stability from original fold reports.

    For each unique four-key found across all folds, creates filtered
    fold summaries (retaining zero-trade folds per key) and runs the
    standard aggregate_stability gates on that key's data.

    Args:
        fold_reports: list of fold report dicts.
        thresholds: optional threshold overrides; defaults to locked set.
        declared_keys: optional list of four-key tuples for full universe.
            Keys with zero traded evidence appear as INSUFFICIENT_DATA.
        expected_fold_ids: optional list of int/str fold IDs that the
            expected ID is present exactly once and all folds completed.

    Returns:
        dict with:
            - keys: dict mapping artifact-key-string -> per-key result
            - n_keys: int
            - classification_summary: dict[str, int] count per classification
            - plan_complete: bool (only set when expected_fold_ids provided)
            - attribution_valid: bool (true when cohort passes all audits)
    """
    attribution_valid = True
    plan_valid = True
    plan_complete_override: bool | None = None
    # Validate complete planned folds and raw trade attribution before grouping.
    try:
        _validate_full_cohort(fold_reports, expected_fold_ids=expected_fold_ids)
    except ValueError:
        attribution_valid = False
        plan_valid = False
        plan_complete_override = False if expected_fold_ids is not None else None
    else:
        plan_complete_override = expected_fold_ids is not None

    # Phase 1: collect all unique four-key tuples across all folds
    all_keys: set[tuple[str, str, str, str]] = set()
    if declared_keys:
        all_keys.update(declared_keys)
    for report in fold_reports:
        for trade in (report.get("trades") or []):
            if trade.get("direction") not in ("BUY", "SELL"):
                continue
            key = _four_key_from_trade(trade)
            all_keys.add(key)

    # Phase 2: for each key, build filtered fold reports and aggregate
    keys_results: dict[str, dict] = {}
    for key in sorted(all_keys):
        key_str = _key_to_artifact_key(key)
        key_fold_summaries: list[dict] = []

        for report in fold_reports:
            all_trades = list(report.get("trades") or [])

            # Retain BUY-only and censored positions as diagnostics for this key.
            filtered_report = dict(report)
            filtered_report["trades"] = [
                trade for trade in all_trades
                if _four_key_from_trade(trade) == key
                and (trade.get("direction") == "BUY"
                     or (trade.get("direction") == "SELL" and trade.get("pnl_pct") is not None))
            ]

            summary = summarize_fold(filtered_report)
            key_fold_summaries.append(summary)

        # Run aggregate stability on this key's fold summaries
        completed = [
            s for s in key_fold_summaries
            if s.get("status") in ("COMPLETED",) and s.get("complete")
        ]

        if not completed:
            aggregate: dict = {
                "classification": "INSUFFICIENT_DATA",
                "n_observed_folds": 0,
                "n_informative_folds": 0,
                "n_failed_folds": len([s for s in key_fold_summaries if not s.get("complete")]),
                "n_total_closed": 0,
                "sample_size_reason": "no completed folds",
            }
        elif not attribution_valid or not plan_valid:
            aggregate = {
                "classification": "INSUFFICIENT_DATA",
                "n_observed_folds": len(completed),
                "n_informative_folds": 0,
                "n_failed_folds": len(key_fold_summaries) - len(completed),
                "n_total_closed": sum(s.get("n_closed", 0) for s in completed),
                "sample_size_reason": "cohort validation failed",
            }
        else:
            aggregate = aggregate_stability(key_fold_summaries, thresholds=thresholds)



        zero_trade_fold_ids = [
            str(s["fold_id"]) for s in key_fold_summaries
            if s.get("n_closed", 0) == 0
        ]
        n_failed = len([s for s in key_fold_summaries if not s.get("complete")])

        # --- UNKNOWN/DEGRADED gating: these keys never STABLE_CANDIDATE ---
        key_has_unknown_degraded = any(
            part in ("UNKNOWN", "DEGRADED") for part in key
        )
        classification = aggregate.get("classification", "INSUFFICIENT_DATA")
        if key_has_unknown_degraded and classification == "STABLE_CANDIDATE":
            classification = "INSUFFICIENT_DATA"
            if "sample_size_reason" not in aggregate or not aggregate["sample_size_reason"]:
                aggregate["sample_size_reason"] = (
                    "key has UNKNOWN or DEGRADED component"
                )

        # --- Attribution/plan gate: failure prevents authorization ---
        if not attribution_valid or (expected_fold_ids is not None and not plan_valid):
            classification = "INSUFFICIENT_DATA"
            aggregate["sample_size_reason"] = "cohort validation failed"

        keys_results[key_str] = {
            "key_tuple": list(key),
            "key_fields": list(FOUR_KEY_FIELDS),
            "n_folds_total": len(key_fold_summaries),
            "n_folds_observed": len(completed),
            "n_informative_folds": aggregate.get("n_informative_folds", 0),
            "n_failed_folds": n_failed,
            "n_total_closed": aggregate.get("n_total_closed", 0),
            "zero_trade_fold_ids": zero_trade_fold_ids,
            "classification": classification,
            "reasons": {
                "sample_size_reason": aggregate.get("sample_size_reason"),
                "coverage_reason": aggregate.get("coverage_reason"),
                "pf_reason": aggregate.get("pf_reason"),
                "expectancy_reason": aggregate.get("expectancy_reason"),
                "drawdown_reason": aggregate.get("drawdown_reason"),
                "worst_expectancy_reason": aggregate.get("worst_expectancy_reason"),
                "concentration_reason": aggregate.get("concentration_reason"),
            },
            "fold_summaries": [
                {
                    "fold_id": s.get("fold_id"),
                    "n_closed": s.get("n_closed", 0),
                    "n_open": s.get("n_open", 0),
                    "n_censored": s.get("n_censored", 0),
                    "expectancy_pct": s.get("expectancy_pct"),
                    "median_trade_pct": s.get("median_trade_pct"),
                    "profit_factor": s.get("profit_factor"),
                    "pf_reason": s.get("pf_reason"),
                }
                for s in key_fold_summaries
            ],
        }

    # Count classifications
    classification_counts: dict[str, int] = {}
    for kr in keys_results.values():
        c = kr["classification"]
        classification_counts[c] = classification_counts.get(c, 0) + 1

    result: dict[str, Any] = {
        "keys": keys_results,
        "n_keys": len(keys_results),
        "classification_summary": classification_counts,
        "attribution_valid": attribution_valid,
    }
    if expected_fold_ids is not None:
        if plan_complete_override is not None:
            result["plan_complete"] = plan_complete_override
    return result


# ---------------------------------------------------------------------------
# Artifact builder with content hash
# ---------------------------------------------------------------------------
def build_per_key_oos_artifact(
    per_key_result: dict,
    run_id: str,
    source_sha: str,
    config_hash: str,
    wf_config_hash: str,
    rule_hashes: dict[str, str],
    run_manifest: dict | None = None,
) -> dict:
    """Build a complete per-key OOS artifact dict with content hash.

    The content_hash is a SHA-256 of the canonical JSON serialization of
    the artifact *excluding* the content_hash field itself, so the hash
    can be verified by recomputation.

    Args:
        per_key_result: result from per_key_oos_stability().
        run_id: run identifier.
        source_sha: git commit SHA of producer code.
        config_hash: runtime config hash.
        wf_config_hash: walk-forward config hash.
        rule_hashes: dict of config section hashes.
        run_manifest: optional run manifest dict for binding.

    Returns:
        dict ready for JSON serialization.
    """
    import hashlib
    import json
    from datetime import datetime, timezone

    manifest_binding: dict[str, str] = {}
    if run_manifest:
        manifest_binding = {
            "manifest_run_id": str(run_manifest.get("run_id", "")),
            "manifest_source_sha": str(run_manifest.get("source_sha", "")),
            "manifest_overall_status": str(run_manifest.get("overall_status") or ""),
            "generation_id": str(run_manifest.get("generation_id") or ""),
            "artifact_path": str(run_manifest.get("artifact_path") or ""),
            "consumed_input_ledger_sha256": str(run_manifest.get("consumed_input_ledger_sha256") or ""),
        }

    # Build artifact without content_hash first
    artifact = {
        "schema_version": DEFAULT_PER_KEY_SCHEMA_VERSION,
        "producer": "oos_stability.per_key_oos_stability",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source_sha": source_sha,
        "config_hash": config_hash,
        "wf_config_hash": wf_config_hash,
        "rule_hashes": dict(rule_hashes),
        "input_manifest_binding": manifest_binding,
        "n_keys": per_key_result.get("n_keys", 0),
        "classification_summary": per_key_result.get("classification_summary", {}),
        "keys": per_key_result.get("keys", {}),
    }

    # Compute content hash over all fields except content_hash
    canonical = json.dumps(
        artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    artifact["content_hash"] = content_hash

    return artifact


# ---------------------------------------------------------------------------
# Verification helper
# ---------------------------------------------------------------------------
def verify_per_key_oos_artifact(
    artifact: dict,
    request_key: tuple[str, str, str, str] | None = None,
    expected_source_sha: str | None = None,
    manifest_path: str | None = None,
) -> dict:
    """Verify a per-key OOS artifact's integrity and key eligibility.

    This function does NOT trust caller-created ``evidence_type`` metadata.
    It validates:
    - Schema version is present and recognized
    - Content hash matches recomputation
    - Producer field is present
    - Run ID and source SHA are present
    - If a key is requested, checks presence and classification
    - If expected_source_sha is provided, checks provenance match
    - Physical manifest and completion marker bind the published generation,
      artifact bytes, all output files, consumed universes and physical inputs.
      Missing provenance or a dirty source tree is non-authorizing.

    Args:
        artifact: loaded artifact dict.
        request_key: optional (regime, theme, pattern, version) tuple
            to check for eligibility.
        expected_source_sha: optional source SHA the caller expects.
        manifest_path: published manifest.json path. Required for valid evidence;
            caller-supplied metadata alone can never authorize a key.

    Returns:
        dict with:
            - valid: bool
            - reasons: list[str] (empty if valid)
            - key_found: bool (only if request_key provided)
            - key_eligible: bool (True only if key has STABLE_CANDIDATE)
            - key_classification: str | None
            - n_keys: int
    """
    import hashlib
    import json

    reasons: list[str] = []

    # 1. Schema version
    schema_ver = artifact.get("schema_version")
    if not schema_ver:
        reasons.append("missing schema_version")
    elif schema_ver != DEFAULT_PER_KEY_SCHEMA_VERSION:
        reasons.append(f"unsupported schema_version: {schema_ver}")

    # 2. Producer
    producer = artifact.get("producer")
    if not producer:
        reasons.append("missing producer")

    # 3. Run ID
    if not artifact.get("run_id"):
        reasons.append("missing run_id")

    # 4. Source SHA
    source_sha = artifact.get("source_sha")
    if not source_sha:
        reasons.append("missing source_sha")

    # 5. Content hash verification
    recorded_hash = artifact.get("content_hash")
    if not recorded_hash:
        reasons.append("missing content_hash")
    else:
        content_blob = {k: v for k, v in artifact.items() if k != "content_hash"}
        canonical = json.dumps(
            content_blob, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if recorded_hash != recomputed:
            reasons.append(
                f"content_hash mismatch: recorded={recorded_hash}, "
                f"recomputed={recomputed}"
            )

    # 6. Expected source SHA provenance
    if expected_source_sha and source_sha:
        if source_sha != expected_source_sha:
            reasons.append(
                f"source_sha mismatch: artifact={source_sha}, "
                f"expected={expected_source_sha}"
            )

    # A caller-built dictionary is not physical evidence. Read the publication
    # marker and manifest from disk, and recompute every committed output hash.
    from pathlib import Path
    manifest_binding = artifact.get("input_manifest_binding") or {}
    try:
        if not manifest_path:
            raise ValueError("missing physical manifest path")
        path = Path(manifest_path).absolute()
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("manifest symlink rejected")
        directory = path.parent
        if directory.name.startswith(".tmp-"):
            raise ValueError("staged generation is not published")
        manifest = json.loads(path.read_text())
        marker_path = directory / "completion.json"
        if marker_path.is_symlink():
            raise ValueError("completion marker symlink rejected")
        marker = json.loads(marker_path.read_text())
        if marker.get("manifest_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("manifest completion hash mismatch")
        for binding_field, manifest_field in (
            ("manifest_run_id", "run_id"), ("manifest_source_sha", "source_sha"),
            ("manifest_overall_status", "overall_status"), ("generation_id", "generation_id"),
            ("artifact_path", "artifact_path"),
            ("consumed_input_ledger_sha256", "consumed_input_ledger_sha256"),
        ):
            if not manifest_binding.get(binding_field) or manifest_binding[binding_field] != manifest.get(manifest_field):
                raise ValueError(f"manifest binding mismatch: {binding_field}")
        if manifest.get("dirty") is not False:
            raise ValueError("manifest source tree is dirty or unverifiable")
        if (manifest.get("run_id") != artifact.get("run_id")
                or manifest.get("source_sha") != source_sha
                or manifest.get("overall_status") != "COMPLETED"
                or manifest.get("completion") is not True
                or marker.get("generation_id") != manifest.get("generation_id")
                or marker.get("run_id") != manifest.get("run_id")):
            raise ValueError("manifest run/source/completion mismatch")
        universes = manifest.get("consumed_universes")
        if not isinstance(universes, dict) or not universes:
            raise ValueError("manifest missing consumed universes")
        for universe in universes.values():
            symbols = universe.get("symbols")
            if not isinstance(symbols, list) or not symbols:
                raise ValueError("manifest consumed universe invalid")
            canonical_universe = json.dumps(symbols, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if hashlib.sha256(canonical_universe.encode("utf-8")).hexdigest() != universe.get("universe_sha256"):
                raise ValueError("manifest consumed universe hash mismatch")
        for field in ("config_hash", "wf_config_hash", "rule_hashes"):
            if manifest.get(field) != artifact.get(field):
                raise ValueError(f"manifest {field} mismatch")
        files = manifest.get("output_files")
        if not isinstance(files, dict) or manifest.get("artifact_path") not in files:
            raise ValueError("manifest missing output file commitments")
        for relative, expected_hash in files.items():
            candidate = directory / relative
            if (Path(relative).is_absolute() or ".." in Path(relative).parts
                    or any(parent.is_symlink() for parent in (candidate, *candidate.parents))
                    or not candidate.resolve().is_relative_to(directory.resolve())):
                raise ValueError("manifest output path escape/symlink rejected")
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"manifest output hash mismatch: {relative}")
        physical_artifact = directory / manifest["artifact_path"]
        if (json.loads(physical_artifact.read_text()) != artifact
                or manifest.get("artifact_sha256") != files[manifest["artifact_path"]]
                or manifest.get("oos_per_key_sha256") != recorded_hash):
            raise ValueError("manifest artifact path/hash mismatch")
        if manifest.get("physical_provenance", {}).get("status") != "VERIFIED":
            raise ValueError("manifest physical input provenance unverifiable")
        from .data import ConsumedInputLedger
        ledger = manifest.get("consumed_input_ledger")
        if not isinstance(ledger, dict) or ledger.get("status") != "VERIFIED":
            raise ValueError("manifest consumed input ledger missing/unverifiable")
        events = ledger.get("events")
        if not isinstance(events, list) or not events:
            raise ValueError("manifest consumed input ledger events missing")
        digest = ConsumedInputLedger.digest(events)
        if digest != ledger.get("sha256") or digest != manifest.get("consumed_input_ledger_sha256"):
            raise ValueError("manifest consumed input ledger hash mismatch")
        root = Path(manifest["project_root"])
        if not root.is_absolute() or ledger.get("root") != str(root):
            raise ValueError("manifest consumed input root invalid")
        checker = ConsumedInputLedger(root)
        fold_ids = {str(f["fold_id"]) for f in manifest["fold_plan"]["folds"]}
        for sequence, event in enumerate(events, 1):
            if type(event.get("sequence")) is not int or event.get("sequence") != sequence or event.get("fold_id") not in fold_ids:
                raise ValueError("manifest ledger event sequence/fold invalid")
            if event.get("verifiable") is not True or event.get("status") not in {"ok", "empty"}:
                raise ValueError("manifest ledger event unverifiable")
            if not all(field in event for field in ("phase", "kind", "logical_key", "requested_range",
                    "returned", "source_type", "dataset_version", "dataset_metadata", "cache_status")):
                raise ValueError("manifest ledger event incomplete")
            if event["source_type"] == "memory":
                origins = event.get("origin_sequences")
                if (not isinstance(origins, list) or not origins or len(origins) != len(set(origins))
                        or any(not isinstance(n, int) or not 0 < n < sequence
                            or events[n - 1]["source_type"] not in {"physical", "cache", "mcp"}
                            or events[n - 1]["fold_id"] != event["fold_id"] for n in origins)):
                    raise ValueError("manifest memory origin linkage invalid")
            elif event["source_type"] == "cache":
                raise ValueError("manifest cache source authenticity unbound")
            elif event['source_type'] == 'mcp':
                if not checker.verify_mcp_event(event):
                    raise ValueError('manifest MCP response commitment incomplete/mismatched')
            elif event["source_type"] != "physical":
                raise ValueError("manifest consumed input source unverifiable")
        inputs = manifest.get("physical_inputs")
        if not inputs or inputs != ConsumedInputLedger.physical_inputs(events) or inputs != ledger.get("physical_inputs"):
            raise ValueError("manifest physical inputs not derived from ledger")
        for entry in inputs:
            if not Path(entry['path']).is_absolute():
                raise ValueError('manifest physical input path must be absolute')
            input_path = checker.checked_path(Path(entry["path"]))
            if hashlib.sha256(input_path.read_bytes()).hexdigest() != entry.get("sha256"):
                raise ValueError("manifest physical input hash mismatch")
        for fold_id in fold_ids:
            fold_relative = f"folds/{fold_id}/consumed_inputs.json"
            if fold_relative not in manifest.get('output_files', {}):
                raise ValueError("manifest fold ledger output commitment missing")
            fold_ledger = json.loads((directory / "folds" / fold_id / "consumed_inputs.json").read_text())
            fold_events = [e for e in events if e["fold_id"] == fold_id]
            if (not fold_events or fold_ledger.get("events") != fold_events
                    or fold_ledger.get("sha256") != ConsumedInputLedger.digest(fold_events)
                    or fold_ledger.get("status") != "VERIFIED"):
                raise ValueError("manifest fold ledger event set mismatch")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        reasons.append(f"physical manifest invalid: {exc}")

    n_keys = artifact.get("n_keys", 0)

    # 8. Key eligibility check
    valid = len(reasons) == 0

    key_found = None
    key_eligible = None
    key_classification = None
    if request_key is not None:
        key_str = _key_to_artifact_key(request_key)
        keys_data = artifact.get("keys", {})
        if key_str in keys_data:
            key_found = True
            key_classification = keys_data[key_str].get("classification", "UNKNOWN")
            # key_eligible requires both a valid artifact AND STABLE_CANDIDATE classification
            key_eligible = valid and key_classification == "STABLE_CANDIDATE"
        else:
            key_found = False
            key_eligible = False
    elif not valid:
        # Even without a request_key, signal that artifact is invalid
        key_eligible = False

    result: dict = {
        "valid": valid,
        "reasons": reasons,
        "n_keys": n_keys,
        "key_found": key_found,
        "key_eligible": key_eligible,
        "key_classification": key_classification,
    }
    return result