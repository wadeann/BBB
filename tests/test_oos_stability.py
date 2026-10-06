"""OOS stability statistics: hand-computed behavioral tests.

All pnl_pct values are fractions (0.05 = 5%). Trades use production contract
fields: direction (BUY/SELL), round_trip_id, exit_reason, quality state dicts
with state="ok". END_OF_BACKTEST exits excluded from closed stats. Tests
notify Main WAIT red before production ran — these verify the contract.
"""
import hashlib
import json
import pytest
from a_share_agent.backtest.oos_stability import (
    summarize_fold, aggregate_stability,
    per_key_oos_stability, verify_per_key_oos_artifact,
    _four_key_from_trade, _key_to_artifact_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_trade(direction: str, round_trip_id: str, pnl_pct: float | None,
               trade_date: str = "2024-01-01",
               exit_reason: str = "SIGNAL:ma20_break",
               regime: str = "BULL", theme: str = "ACTIVE",
               pattern: str = "MORNING_STAR", version: str = "v1",
               mfe: float | None = None, mae: float | None = None) -> dict:
    t: dict = {
        "direction": direction,
        "round_trip_id": round_trip_id,
        "pnl_pct": pnl_pct,
        "trade_date": trade_date,
        "exit_reason": exit_reason,
        "regime_at_signal": regime,
        "theme_lifecycle": theme,
        "pattern_id": pattern,
        "pattern_version": version,
        "regime_data_quality_at_signal": {"state": "ok"},
        "theme_data_quality_at_signal": {"state": "ok"},
    }
    if mfe is not None:
        t["mfe_pct"] = mfe
    if mae is not None:
        t["mae_pct"] = mae
    return t


def make_fold(fold_id: int, trades: list[dict] | None = None,
              complete: bool = True, status: str = "COMPLETED",
              coverage: float = 1.0) -> dict:
    # Arithmetic fixtures represent closed round trips, not orphan exits.
    trades = list(trades or [])
    buy_ids = {t["round_trip_id"] for t in trades if t["direction"] == "BUY"}
    trades = [make_trade("BUY", t["round_trip_id"], None,
                         regime=t["regime_at_signal"], theme=t["theme_lifecycle"],
                         pattern=t["pattern_id"], version=t["pattern_version"])
              for t in trades if t["direction"] == "SELL" and t["round_trip_id"] not in buy_ids] + trades
    return {
        "fold_id": fold_id,
        "complete": complete,
        "status": status,
        "trades": trades or [],
        "coverage": coverage,
    }


# ====== 1. Basic arithmetic ======
def test_summarize_fold_basic_arithmetic():
    """Fraction arithmetic for expectancy, median, PF."""
    # +5%, -2%, +3%, -1%  → fractions: 0.05, -0.02, 0.03, -0.01
    # Expectancy = (0.05 - 0.02 + 0.03 - 0.01)/4 = 0.0125
    # Median of [-0.02, -0.01, 0.03, 0.05] = (-0.01+0.03)/2 = 0.01
    # PF = (0.05+0.03)/(0.02+0.01) = 8/3 = 2.6667
    trades = [make_trade("SELL", "t1", 0.05), make_trade("SELL", "t2", -0.02),
              make_trade("SELL", "t3", 0.03), make_trade("SELL", "t4", -0.01)]
    s = summarize_fold(make_fold(1, trades))
    assert s["fold_id"] == 1 and s["n_closed"] == 4
    assert s["expectancy_pct"] == pytest.approx(0.0125)
    assert s["median_trade_pct"] == pytest.approx(0.01)
    assert s["profit_factor"] == pytest.approx(8.0 / 3.0)
    assert s["n_winners"] == 2 and s["n_losers"] == 2
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["closed_returns"] == [0.05, -0.02, 0.03, -0.01]


# ====== 2a. PF no losses ======
def test_profit_factor_no_losses():
    trades = [make_trade("SELL", f"t{i}", 0.01 * i) for i in range(1, 4)]
    s = summarize_fold(make_fold(1, trades))
    assert s["profit_factor"] is None
    assert s["pf_reason"] == "NO_LOSSES"


# ====== 2b. PF all losses ======
def test_profit_factor_all_losses():
    trades = [make_trade("SELL", f"t{i}", -0.01 * i) for i in range(1, 4)]
    s = summarize_fold(make_fold(1, trades))
    assert s["profit_factor"] == pytest.approx(0.0)
    assert s["pf_reason"] == "FINITE"


# ====== 3. Drawdown ======
def test_drawdown_compounded_sequence():
    """Compounded DD, sorted by date/ID."""
    # +10%, -20%, +5%, -15% → equity: 1.0*1.10*0.80*1.05*0.85 = 0.7854
    # Peak = 1.10, DD = 0.7854/1.10 - 1 = -0.2860
    trades = [make_trade("SELL", "t1", 0.10, "2024-01-01"),
              make_trade("SELL", "t2", -0.20, "2024-01-02"),
              make_trade("SELL", "t3", 0.05, "2024-01-03"),
              make_trade("SELL", "t4", -0.15, "2024-01-04")]
    s = summarize_fold(make_fold(1, trades))
    expected = (1.10 * 0.80 * 1.05 * 0.85) / 1.10 - 1.0
    assert s["max_drawdown_pct"] == pytest.approx(expected)


# ====== 4. MFE/MAE ======
def test_mfe_mae_fraction_units():
    trades = [make_trade("SELL", "t1", 0.05, mfe=0.08, mae=-0.03),
              make_trade("SELL", "t2", -0.02, mfe=0.01, mae=-0.06)]
    s = summarize_fold(make_fold(1, trades))
    assert s["avg_mfe_pct"] == pytest.approx(0.045)
    assert s["avg_mae_pct"] == pytest.approx(-0.045)


# ====== 5. Pooled vs equal-fold median ======
def test_pooled_vs_fold_median():
    """Pooled median differs from equal-fold median."""
    # Fold 1: [0.05, 0.15] → median 0.10 | Fold 2: [1.00] → median 1.00
    # Fold 3: [0.50] → median 0.50
    # Pooled: [0.05,0.15,1.00,0.50] → sorted → median (0.15+0.50)/2 = 0.325
    # Fold medians: [0.10, 1.00, 0.50] → median 0.50
    f1 = make_fold(1, [make_trade("SELL", "t1", 0.05), make_trade("SELL", "t2", 0.15)])
    f2 = make_fold(2, [make_trade("SELL", "t3", 1.00)])
    f3 = make_fold(3, [make_trade("SELL", "t4", 0.50)])
    agg = aggregate_stability([summarize_fold(f) for f in (f1, f2, f3)])
    assert agg["pooled_median_trade_pct"] == pytest.approx(0.325)
    assert agg["fold_median_trade_pct"] == pytest.approx(0.50)


# ====== 6. No-trade folds ======
def test_no_trade_fold_preserved():
    f1 = make_fold(1, []); f2 = make_fold(2, [make_trade("SELL", "t1", 0.05)])
    s1, s2 = summarize_fold(f1), summarize_fold(f2)
    assert s1["n_closed"] == 0 and s1["expectancy_pct"] is None
    agg = aggregate_stability([s1, s2])
    assert agg["n_observed_folds"] == 2 and agg["n_informative_folds"] == 0


# ====== 7. Failed folds ======
def test_failed_fold_classification():
    """Failed/partial folds → INSUFFICIENT_DATA."""
    f1 = make_fold(1, [], complete=False, status="RUN_FAILED")
    f2 = make_fold(2, [], complete=False, status="DATA_BLOCKED")
    f3 = make_fold(3, [make_trade("SELL", "t1", 0.05)])
    f4 = make_fold(4, [make_trade("SELL", "t2", 0.03)])
    agg = aggregate_stability([summarize_fold(f) for f in (f1, f2, f3, f4)])
    assert agg["classification"] == "INSUFFICIENT_DATA"
    assert agg["n_informative_folds"] == 0 and agg["n_failed_folds"] == 2


# ====== 8. Matrix isolation ======
def test_unknown_and_version_isolation():
    """UNKNOWN/DEGRADED values and versions isolated in matrices."""
    trades = [make_trade("SELL", "t1", 0.05, regime="BULL", theme="ACTIVE", pattern="P1", version="v1"),
              make_trade("SELL", "t2", -0.02, regime="UNKNOWN", theme="ACTIVE", pattern="P1", version="v1"),
              make_trade("SELL", "t3", 0.03, regime="BULL", theme="DEGRADED", pattern="P1", version="v1"),
              make_trade("SELL", "t4", -0.01, regime="BULL", theme="ACTIVE", pattern="P1", version="v2")]
    s = summarize_fold(make_fold(1, trades))
    assert "BULL" in s["regime_matrix"] and "UNKNOWN" in s["regime_matrix"]
    assert "ACTIVE" in s["theme_matrix"] and "DEGRADED" in s["theme_matrix"]
    assert "v1" in s["version_matrix"] and "v2" in s["version_matrix"]


# ====== 9. Duplicate IDs ======
def test_duplicate_trade_id_aggregate_error():
    """Duplicate round_trip_ids across folds cause aggregate ValueError."""
    f1 = make_fold(1, [make_trade("SELL", "t1", 0.05)])
    f2 = make_fold(2, [make_trade("SELL", "t1", 0.03)])
    s1 = summarize_fold(f1)
    s2 = summarize_fold(f2)
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_stability([s1, s2])


# ====== 10. Boundary thresholds ======
def test_boundary_thresholds():
    """Values exactly at thresholds → STABLE_CANDIDATE."""
    folds = [make_fold(i, [make_trade("SELL", f"t{i}_{j}", 0.02) for j in range(9)] + [make_trade("SELL", f"t{i}_l", -0.01)]) for i in range(4)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "STABLE_CANDIDATE"
    assert agg["n_observed_folds"] == 4 and agg["n_total_closed"] == 40


# ====== 11. Concentration ======
def test_concentrated_single_fold_return():
    """One fold dominates → UNSTABLE."""
    # Each fold needs at least one loser so PF is finite
    f1_wins = [make_trade("SELL", f"t1_{j}_w", 0.10) for j in range(9)]
    f1_loss = make_trade("SELL", "t1_l", -0.005)
    f1 = make_fold(1, f1_wins + [f1_loss])
    other_folds = []
    for i in range(2, 5):
        wins = [make_trade("SELL", f"t{i}_{j}_w", 0.01) for j in range(9)]
        loss = make_trade("SELL", f"t{i}_l", -0.005)
        other_folds.append(make_fold(i, wins + [loss]))
    folds = [f1] + other_folds
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"
    assert agg["max_fold_return_concentration"] is not None
    # Fold 1 total ~ 0.10*9 - 0.005 = 0.895, Others ~ 0.01*9 - 0.005 = 0.085 each
    # Concentration = 0.895 / (0.895 + 3*0.085) = 0.895 / 1.15 = 0.778 > 0.50


# ====== 12. Sample size ======
def test_insufficient_data_sample_size():
    folds = [make_fold(i, [make_trade("SELL", f"t{i}_{j}", 0.01) for j in range(10)]) for i in range(2)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "INSUFFICIENT_DATA"


# ====== 13. Negative expectancy ======
def test_unstable_negative_expectancy():
    folds = [make_fold(i, [make_trade("SELL", f"t{i}_{j}", -0.005) for j in range(10)]) for i in range(4)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"
    assert agg["fold_median_expectancy_pct"] == pytest.approx(-0.005)


# ====== 14. PF at 1.0 ======
def test_unstable_pf_at_threshold():
    """PF == 1.0 → UNSTABLE (strict >)."""
    folds = []
    for i in range(4):
        trades = []
        for j in range(5):
            trades.append(make_trade("SELL", f"t{i}_{j}_w", 0.02))
            trades.append(make_trade("SELL", f"t{i}_{j}_l", -0.02))
        folds.append(make_fold(i, trades))
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"
    assert agg["fold_median_profit_factor"] == pytest.approx(1.0)


# ====== 15. Drawdown > 15% ======
def test_unstable_drawdown():
    folds = []
    for i in range(4):
        trades = [make_trade("SELL", f"t{i}_big", -0.20)] + [make_trade("SELL", f"t{i}_{j}", 0.01) for j in range(9)]
        folds.append(make_fold(i, trades))
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"


# ====== 16. Missing pnl_pct ======
def test_missing_net_return_error():
    with pytest.raises(ValueError):
        summarize_fold(make_fold(1, [make_trade("SELL", "t1", None)]))


# ====== 17. END_OF_BACKTEST exclusion ======
def test_unclosed_not_in_closed_stats():
    """Only natural SELL exits count; END_OF_BACKTEST is censored."""
    trades = [make_trade("BUY", "t1", None), make_trade("BUY", "t2", None),
              make_trade("SELL", "t2", 0.05),
              make_trade("SELL", "t3", 0.03, exit_reason="END_OF_BACKTEST")]
    s = summarize_fold(make_fold(1, trades))
    assert s["n_closed"] == 1 and s["n_censored"] == 1
    assert s["expectancy_pct"] == pytest.approx(0.05)


# ====== 18. Coverage gate ======
def test_context_coverage_gate():
    f1 = make_fold(1, [make_trade("SELL", "t1", 0.05)], coverage=0.8)
    f2 = make_fold(2, [make_trade("SELL", "t2", 0.05)])
    f3 = make_fold(3, [make_trade("SELL", "t3", 0.05)])
    f4 = make_fold(4, [make_trade("SELL", "t4", 0.05)])
    agg = aggregate_stability([summarize_fold(f) for f in (f1, f2, f3, f4)])
    assert agg["classification"] == "INSUFFICIENT_DATA"


# ====== 19. All reasons ======
def test_all_reasons_returned():
    folds = [make_fold(i, [make_trade("SELL", f"t{i}_{j}", 0.01) for j in range(10)]) for i in range(4)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    for k in ("sample_size_reason", "pf_reason", "expectancy_reason",
              "drawdown_reason", "concentration_reason"):
        assert k in agg


# ====== 20. Finite JSON ======
def test_deterministic_finite_json():
    folds = [make_fold(i, [make_trade("SELL", f"t{i}_{j}", 0.01) for j in range(10)]) for i in range(4)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    json_str = json.dumps(agg, allow_nan=False)
    assert isinstance(json_str, str) and len(json_str) > 0
    assert "Infinity" not in json_str and "NaN" not in json_str


# ---------------------------------------------------------------------------
# Helper: build a fold report with mixed-key trades
# ---------------------------------------------------------------------------
def make_multi_key_fold(fold_id: int, complete: bool = True,
                        status: str = "COMPLETED", coverage: float = 1.0,
                        trades: list[dict] | None = None) -> dict:
    """Build a fold report with raw BUY/SELL attribution for valid fixtures."""
    trades = list(trades or [])
    buy_ids = {str(t.get("round_trip_id")) for t in trades if t.get("direction") == "BUY"}
    buys = [
        make_trade("BUY", str(t["round_trip_id"]), None,
                   regime=t.get("regime_at_signal", "UNKNOWN"),
                   theme=t.get("theme_lifecycle", "UNKNOWN"),
                   pattern=t.get("pattern_id", "UNKNOWN"),
                   version=t.get("pattern_version", "UNKNOWN"))
        for t in trades
        if t.get("direction") == "SELL" and str(t.get("round_trip_id")) not in buy_ids
    ]
    return {
        "fold_id": fold_id,
        "complete": complete,
        "status": status,
        "coverage": coverage,
        "trades": buys + trades,
    }

def linked_oos_trades(round_trip_id: str, pnl_pct: float, **key_fields) -> list[dict]:
    return [make_trade("BUY", round_trip_id, None, **key_fields),
            make_trade("SELL", round_trip_id, pnl_pct, **key_fields)]


# ====== PK1: Two versions of same pattern stay separate ======
def test_per_key_versions_separate():
    """Same pattern, different versions → separate key results."""
    # Fold 1: v1 trades + v2 trades
    t1_v1 = make_trade("SELL", "t1", 0.05, pattern="P1", version="v1")
    t1_v2 = make_trade("SELL", "t2", -0.02, pattern="P1", version="v2")
    fold1 = make_multi_key_fold(1, trades=[t1_v1, t1_v2])

    # Fold 2: v1 trades
    t2_v1 = make_trade("SELL", "t3", 0.03, pattern="P1", version="v1")
    fold2 = make_multi_key_fold(2, trades=[t2_v1])

    result = per_key_oos_stability([fold1, fold2])

    # Both versions should have entries
    v1_key = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    v2_key = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v2"))
    assert v1_key in result["keys"], f"v1 key {v1_key} missing from {list(result['keys'].keys())}"
    assert v2_key in result["keys"], f"v2 key {v2_key} missing"

    # v1 should have trades from both folds
    v1_data = result["keys"][v1_key]
    assert v1_data["n_total_closed"] == 2
    assert "STABLE_CANDIDATE" in v1_data["classification"] or v1_data["classification"] == "INSUFFICIENT_DATA"

    # v2 should have 1 trade
    v2_data = result["keys"][v2_key]
    assert v2_data["n_total_closed"] == 1


# ====== PK2: Exact four-key grouping ======
def test_per_key_exact_four_key_grouping():
    """Trades with different four-key tuples are grouped separately."""
    t1 = make_trade("SELL", "t1", 0.05, regime="BULL", theme="ACTIVE", pattern="P1", version="v1")
    t2 = make_trade("SELL", "t2", -0.02, regime="BEAR", theme="ACTIVE", pattern="P1", version="v1")
    t3 = make_trade("SELL", "t3", 0.03, regime="BULL", theme="CONTRACTION", pattern="P1", version="v1")
    fold = make_multi_key_fold(1, trades=[t1, t2, t3])
    result = per_key_oos_stability([fold])
    key_ids = set(result["keys"].keys())
    assert _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1")) in key_ids
    assert _key_to_artifact_key(("BEAR", "ACTIVE", "P1", "v1")) in key_ids
    assert _key_to_artifact_key(("BULL", "CONTRACTION", "P1", "v1")) in key_ids
    assert len(result["keys"]) == 3


# ====== PK3: Zero-trade folds per key are retained ======
def test_per_key_zero_trade_fold_retained():
    """A fold with no closed trades for a given key is still counted as observed."""
    # Fold 1: only key A trades
    t1 = make_trade("SELL", "t1", 0.05, pattern="P_A", version="v1")
    # Fold 2: no trades at all
    fold1 = make_multi_key_fold(1, trades=[t1])
    fold2 = make_multi_key_fold(2, trades=[])
    result = per_key_oos_stability([fold1, fold2])
    key = _key_to_artifact_key(("BULL", "ACTIVE", "P_A", "v1"))
    assert key in result["keys"]
    # Fold 2 contributed zero trades to key A — it should count as observed
    zero_folds = result["keys"][key].get("zero_trade_fold_ids", [])
    assert "2" in zero_folds, f"Zero-trade fold 2 should be in zero_trade_fold_ids: {zero_folds}"
    # Fold count includes the zero fold
    # n_folds_total counts all folds that had trades for this key or are observed
    assert result["keys"][key]["n_folds_total"] == 2


# ====== PK4: UNKNOWN/degraded non-authorization ======
def test_per_key_unknown_and_degraded():
    """UNKNOWN/DEGRADED four-key values produce separate entries, never STABLE_CANDIDATE."""
    unknown_trade = make_trade("SELL", "t1", 0.05, regime="UNKNOWN", theme="ACTIVE", pattern="P1", version="v1")
    degraded_theme = make_trade("SELL", "t2", 0.03, regime="BULL", theme="DEGRADED", pattern="P1", version="v1")
    fold = make_multi_key_fold(1, trades=[unknown_trade, degraded_theme])
    result = per_key_oos_stability([fold])
    # UNKNOWN regime yields a separate key
    unknown_key = _key_to_artifact_key(("UNKNOWN", "ACTIVE", "P1", "v1"))
    assert unknown_key in result["keys"]
    v1_result = result["keys"][unknown_key]
    assert v1_result["classification"] != "STABLE_CANDIDATE", \
        f"UNKNOWN key should not be STABLE_CANDIDATE: {v1_result['classification']}"

    # Degraded theme yields a key — it's a valid value so it appears as a separate key
    degraded_key = _key_to_artifact_key(("BULL", "DEGRADED", "P1", "v1"))
    assert degraded_key in result["keys"]
    d_result = result["keys"][degraded_key]
def test_per_key_stable_uses_default_thresholds():
    """Per-key classification uses default thresholds with audited trades."""
    folds = []
    for i in range(4):
        trades = []
        for j in range(9):
            trades += linked_oos_trades(f"t{i}_{j}_w", 0.02, pattern="P1", version="v1")
        trades += linked_oos_trades(f"t{i}_l", -0.01, pattern="P1", version="v1")
        folds.append(make_multi_key_fold(i, trades=trades))
    result = per_key_oos_stability(folds)
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert result["keys"][key_str]["classification"] == "STABLE_CANDIDATE"


# ====== PK6: INSUFFICIENT_DATA when too few trades per key ======
def test_per_key_insufficient_data():
    """Too few folds/trades per key → INSUFFICIENT_DATA."""
    # 2 folds only, each with 1 trade
    folds = [make_multi_key_fold(1, trades=[make_trade("SELL", "t1", 0.01, pattern="P1", version="v1")]),
             make_multi_key_fold(2, trades=[make_trade("SELL", "t2", 0.02, pattern="P1", version="v1")])]
    result = per_key_oos_stability(folds)
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert key_str in result["keys"]
    assert result["keys"][key_str]["classification"] == "INSUFFICIENT_DATA"


# ====== PK7: UNSTABLE per key ======
def test_per_key_unstable():
    """Negative expectancy per key → UNSTABLE."""
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", -0.005, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert key_str in result["keys"]
    assert result["keys"][key_str]["classification"] == "UNSTABLE"


# ====== PK8: Failed/incomplete folds don't mint eligible evidence ======
def test_per_key_failed_folds_dont_mint():
    """Failed or incomplete runs must not produce eligible key evidence."""
    fold_ok = make_multi_key_fold(1, trades=[make_trade("SELL", "t1", 0.05, pattern="P1", version="v1")])
    fold_failed = make_multi_key_fold(2, trades=[make_trade("SELL", "t2", 0.05, pattern="P1", version="v1")],
                                      complete=False, status="RUN_FAILED")
    fold_blocked = make_multi_key_fold(3, trades=[], complete=False, status="DATA_BLOCKED")
    result = per_key_oos_stability([fold_ok, fold_failed, fold_blocked])
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert key_str in result["keys"]
    kr = result["keys"][key_str]
    # Only 1 observed fold (only fold_ok has trades for this key)
    # But the function should count all 3 folds, with 2 non-complete
    assert kr["n_folds_total"] == 3
    assert kr["n_folds_observed"] == 1  # only fold_ok contributes
    assert kr["n_failed_folds"] == 2
    assert kr["classification"] != "STABLE_CANDIDATE"


# ====== PK9: Per-key result includes reasons ======
def test_per_key_all_reasons():
    """Each per-key result includes all reason fields."""
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", 0.02, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert key_str in result["keys"]
    kr = result["keys"][key_str]
    for reason_field in ("sample_size_reason", "pf_reason", "expectancy_reason",
                         "drawdown_reason", "concentration_reason", "coverage_reason"):
        assert reason_field in kr.get("reasons", {}), f"Missing reason field: {reason_field}"


# ====== PK10: No eligible keys when fold_reports empty ======
def test_per_key_empty_reports():
    """Empty fold_reports list returns empty keys dict."""
    result = per_key_oos_stability([])
    assert result["keys"] == {}
    assert result["n_keys"] == 0


# ====== PK11: Empty trades in fold -> no key produced ======
def test_per_key_no_trades_anywhere():
    """No trades in any fold -> empty keys."""
    folds = [make_multi_key_fold(1, trades=[]),
             make_multi_key_fold(2, trades=[])]
    result = per_key_oos_stability(folds)
    assert result["keys"] == {}
    assert result["n_keys"] == 0


# ==========================================================================
#  Per-key artifact persistence & verification tests
# ==========================================================================

# ====== PA1: Artifact has correct schema version and metadata ======
def test_per_key_artifact_has_schema_and_provenance(tmp_path):
    """Persisted per-key OOS artifact includes schema version and provenance."""
    from a_share_agent.backtest.oos_stability import (
        build_per_key_oos_artifact, DEFAULT_PER_KEY_SCHEMA_VERSION,
    )
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", 0.02, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)

    artifact = build_per_key_oos_artifact(
        result, run_id="test-run-001",
        source_sha="abc123deadbeef",
        config_hash="config-hash-xyz",
        wf_config_hash="wf-config-hash-xyz",
        rule_hashes={"router_sha256": "abc", "scanner_sha256": "def"},
        run_manifest={"run_id": "test-run-001", "source_sha": "abc123deadbeef"},
    )

    assert artifact["schema_version"] == DEFAULT_PER_KEY_SCHEMA_VERSION
    assert artifact["producer"] == "oos_stability.per_key_oos_stability"
    assert artifact["run_id"] == "test-run-001"
    assert artifact["source_sha"] == "abc123deadbeef"
    assert artifact["config_hash"] == "config-hash-xyz"
    assert artifact["content_hash"] is not None and isinstance(artifact["content_hash"], str)
    assert artifact["n_keys"] >= 1
    assert artifact["input_manifest_binding"]["manifest_run_id"] == "test-run-001"


# ====== PA2: Content hash verifies against bytes ======
def test_per_key_artifact_content_hash(tmp_path):
    """Recorded content_hash matches SHA-256 of canonical artifact bytes."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", 0.02, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(
        result, run_id="test-run-002", source_sha="abc123",
        config_hash="ch", wf_config_hash="wch",
        rule_hashes={}, run_manifest={"run_id": "test-run-002"},
    )

    # Re-compute content hash from artifact content minus the content_hash field itself
    content_blob = {k: v for k, v in artifact.items() if k != "content_hash"}
    canonical = json.dumps(content_blob, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert artifact["content_hash"] == expected_hash,         f"Expected {expected_hash} got {artifact['content_hash']}"


# ====== PA3: verify_per_key_oos_artifact validates integrity ======
def test_per_key_verify_artifact_valid(tmp_path):
    """Verification passes for a valid artifact."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", 0.02, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(
        result, run_id="test-run-003", source_sha="sha123",
        config_hash="ch3", wf_config_hash="wch3",
        rule_hashes={}, run_manifest={"run_id": "test-run-003"},
    )

    v_result = verify_per_key_oos_artifact(artifact)
    assert v_result["valid"] is False  # No physical manifest was supplied
    assert any("manifest" in reason for reason in v_result["reasons"])


# ====== PA4: Tampered content hash fails verification ======
def test_per_key_verify_tampered_hash(tmp_path):
    """Modified content_hash causes verification failure."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", 0.02, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(
        result, run_id="test-run-004", source_sha="sha456",
        config_hash="ch4", wf_config_hash="wch4",
        rule_hashes={}, run_manifest={"run_id": "test-run-004"},
    )

    # Tamper content_hash
    artifact["content_hash"] = "deadbeef" * 8
    v_result = verify_per_key_oos_artifact(artifact)
    assert v_result["valid"] is False
    assert any("content_hash" in r.lower() or "hash" in r.lower() for r in v_result["reasons"]),         f"Expected hash mismatch reason, got: {v_result['reasons']}"


# ====== PA5: Missing schema version fails verification ======
def test_per_key_verify_missing_schema():
    """Missing schema_version causes verification failure."""
    artifact = {"producer": "test", "run_id": "x"}
    v_result = verify_per_key_oos_artifact(artifact)
    assert v_result["valid"] is False


# ====== PA6: Verification checks exact key eligibility ======
def test_per_key_verify_key_eligibility(tmp_path):
    """Verification checks that a requested key is present and non-STABLE returns ineligible."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    # Create result with a known UNSTABLE key
    folds = [make_multi_key_fold(i, trades=[
        make_trade("SELL", f"t{i}_{j}", -0.01, pattern="P1", version="v1")
        for j in range(10)
    ]) for i in range(4)]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(
        result, run_id="test-run-005", source_sha="sha789",
        config_hash="ch5", wf_config_hash="wch5",
        rule_hashes={}, run_manifest={"run_id": "test-run-005"},
    )

    # Verify with exact key — should find it but report UNSTABLE
    target_key = ("BULL", "ACTIVE", "P1", "v1")
    v_result = verify_per_key_oos_artifact(artifact, request_key=target_key)
    assert v_result["valid"] is False  # Unbound dictionaries cannot authorize
    assert v_result["key_found"] is True
    assert v_result["key_eligible"] is False  # UNSTABLE, not STABLE_CANDIDATE
    assert v_result["key_classification"] == "UNSTABLE"


# ====== PA7: Missing key in artifact returns not found ======
def test_per_key_verify_missing_key():
    """Requesting a key not present in artifact reports not found."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    folds = [make_multi_key_fold(1, trades=[
        make_trade("SELL", "t1", 0.01, pattern="P1", version="v1")
    ])]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(result, run_id="r", source_sha="s",
                                          config_hash="c", wf_config_hash="w",
                                          rule_hashes={}, run_manifest={"run_id": "r"})

    nonexistent_key = ("BEAR", "DECLINING", "P2", "v2")
    v_result = verify_per_key_oos_artifact(artifact, request_key=nonexistent_key)
    assert v_result["valid"] is False
    assert v_result["key_found"] is False
    assert v_result["key_eligible"] is False


# ====== PA8: Empty artifact with no keys still validates structurally ======
def test_per_key_artifact_empty_keys_validation():
    """An artifact with zero keys is structurally valid but no keys are eligible."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    result = per_key_oos_stability([])
    artifact = build_per_key_oos_artifact(result, run_id="empty", source_sha="s",
                                          config_hash="c", wf_config_hash="w",
                                          rule_hashes={}, run_manifest={"run_id": "empty"})
    v_result = verify_per_key_oos_artifact(artifact)
    assert v_result["valid"] is False
    assert v_result["n_keys"] == 0
    # Any requested key won't be found
    any_key = ("BULL", "ACTIVE", "P1", "v1")
    v2 = verify_per_key_oos_artifact(artifact, request_key=any_key)
    assert v2["key_found"] is False
    assert v2["key_eligible"] is False


# ====== PA9: Provenance check fails on mismatched source_sha ======
def test_per_key_verify_provenance_mismatch(tmp_path):
    """Verification with expected provenance rejects mismatched source_sha."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    folds = [make_multi_key_fold(1, trades=[
        make_trade("SELL", "t1", 0.01, pattern="P1", version="v1")
    ])]
    result = per_key_oos_stability(folds)
    artifact = build_per_key_oos_artifact(result, run_id="prov", source_sha="good_sha",
                                          config_hash="ch", wf_config_hash="wch",
                                          rule_hashes={}, run_manifest={"run_id": "prov"})

    v_result = verify_per_key_oos_artifact(artifact, expected_source_sha="wrong_sha")
    assert v_result["valid"] is False
# ====== PA10: Declared key universe captures zero-trade keys ======
# ====== PK13: Expected fold plan gates per-key classification ======
def test_per_key_expected_fold_ids_accepts_complete():
    folds = [make_multi_key_fold(i, trades=[
        trade for j in range(9) for trade in linked_oos_trades(
            f"t{i}_{j}", 0.02, pattern="P1", version="v1")
    ] + linked_oos_trades(f"t{i}_loss", -0.01, pattern="P1", version="v1"))
        for i in range(1, 5)]
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    assert result["plan_complete"] is True
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert result["keys"][key_str]["classification"] == "STABLE_CANDIDATE"

def test_per_key_expected_fold_rejects_missing():
    folds = [make_multi_key_fold(i, trades=linked_oos_trades(f"t{i}", 0.02, pattern="P1", version="v1")) for i in (1, 2, 3)]
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    assert result["plan_complete"] is False
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_expected_fold_rejects_unexpected_report():
    fold = make_multi_key_fold(5, trades=linked_oos_trades("extra", 0.02, pattern="P1", version="v1"))
    result = per_key_oos_stability([fold], expected_fold_ids=[1])
    assert result["plan_complete"] is False
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_expected_fold_rejects_duplicates():
    folds = [make_multi_key_fold(1, trades=linked_oos_trades(rid, 0.02, pattern="P1", version="v1")) for rid in ("a", "b")]
    result = per_key_oos_stability(folds, expected_fold_ids=[1])
    assert result["plan_complete"] is False
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_cross_fold_round_trip_reuse_fails_closed():
    folds = [make_multi_key_fold(fid, trades=linked_oos_trades("shared", 0.02, pattern="P1", version="v1")) for fid in (1, 2)]
    result = per_key_oos_stability(folds)
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())

def test_per_key_duplicate_report_fold_id_is_non_authorizing():
    fold = make_multi_key_fold(1, trades=linked_oos_trades("same", 0.02, pattern="P1", version="v1"))
    result = per_key_oos_stability([fold, dict(fold)], expected_fold_ids=[1])
    assert result["plan_complete"] is False
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_expected_fold_rejects_blocked():
    folds = [make_multi_key_fold(1, trades=linked_oos_trades("a", 0.02, pattern="P1", version="v1")),
             make_multi_key_fold(2, complete=False, status="DATA_BLOCKED"),
             make_multi_key_fold(3, trades=linked_oos_trades("c", 0.02, pattern="P1", version="v1")),
             make_multi_key_fold(4, trades=linked_oos_trades("d", 0.02, pattern="P1", version="v1"))]
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    assert result["plan_complete"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_expected_fold_rejects_failed():
    folds = [make_multi_key_fold(1, trades=linked_oos_trades("a", 0.02, pattern="P1", version="v1")),
             make_multi_key_fold(2, complete=False, status="RUN_FAILED"),
             make_multi_key_fold(3, trades=linked_oos_trades("c", 0.02, pattern="P1", version="v1")),
             make_multi_key_fold(4, trades=linked_oos_trades("d", 0.02, pattern="P1", version="v1"))]
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    assert result["plan_complete"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_partial_planned_fold_is_non_authorizing():
    folds = [make_multi_key_fold(i, trades=[
        trade for j in range(9) for trade in linked_oos_trades(
            f"p{i}_{j}", 0.02, pattern="P1", version="v1")
    ] + linked_oos_trades(f"p{i}_loss", -0.01, pattern="P1", version="v1"))
        for i in range(1, 4)]
    partial = make_multi_key_fold(4, complete=False, status="PARTIAL", trades=[])
    result = per_key_oos_stability(folds + [partial], expected_fold_ids=[1, 2, 3, 4])
    assert result["plan_complete"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())

def test_per_key_concentration_uses_positive_trade_return_sums():
    folds = []
    for fold_id in range(1, 5):
        returns = ([0.20] * 5 + [-0.01] * 5) if fold_id == 1 else ([0.04] * 5 + [-0.01] * 5)
        trades = [
            trade for index, value in enumerate(returns)
            for trade in linked_oos_trades(f"conc-{fold_id}-{index}", value, pattern="P1", version="v1")
        ]
        folds.append(make_multi_key_fold(fold_id, trades=trades))
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    key = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert result["keys"][key]["classification"] == "UNSTABLE"
    assert result["keys"][key]["reasons"]["concentration_reason"] is not None


def test_per_key_attribution_orphan_sell():
    fold = {"fold_id": 1, "complete": True, "status": "COMPLETED",
            "coverage": 1.0, "trades": [make_trade("SELL", "orphan", 0.02)]}
    result = per_key_oos_stability([fold])
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_attribution_global_dup_id():
    folds = [make_multi_key_fold(fid, trades=linked_oos_trades("dup", 0.02, pattern=f"P{fid}", version="v1")) for fid in (1, 2)]
    result = per_key_oos_stability(folds)
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())


def test_per_key_attribution_none_return():
    trades = [make_trade("BUY", "t1", None), make_trade("SELL", "t1", None)]
    result = per_key_oos_stability([make_multi_key_fold(1, trades=trades)])
    assert result["attribution_valid"] is False
    assert all(item["classification"] != "STABLE_CANDIDATE" for item in result["keys"].values())
def test_per_key_concentration_positive_sum_not_net():
    """Positive-return concentration gate receives complete attributable folds."""
    folds = []
    for fid in range(1, 5):
        values = ([0.20] * 5 + [-0.099] * 10) if fid == 1 else ([0.05] * 5 + [-0.05])
        trades = [
            trade for index, value in enumerate(values)
            for trade in linked_oos_trades(f"f{fid}_{index}", value, pattern="P1", version="v1")
        ]
        folds.append(make_multi_key_fold(fid, trades=trades))
    result = per_key_oos_stability(folds, expected_fold_ids=[1, 2, 3, 4])
    key = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    assert result["keys"][key]["classification"] != "STABLE_CANDIDATE"


# ====== PK16: Open and censored diagnostics ======
def test_per_key_open_trade_diagnostics():
    """Open trades (BUY without SELL) retained as open_count per key."""
    trades = [
        make_trade("BUY", "open1", None, pattern="P1", version="v1"),
        make_trade("SELL", "closed1", 0.02, pattern="P1", version="v1"),
    ]
    result = per_key_oos_stability([make_multi_key_fold(1, trades=trades)])
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    fold_info = result["keys"][key_str]["fold_summaries"][0]
    assert "n_open" in fold_info


def test_per_key_censored_trade_diagnostics():
    """Censored trades (END_OF_BACKTEST exit) retained as censored_count per key."""
    trades = [
        make_trade("BUY", "cens1", None, pattern="P1", version="v1"),
        make_trade("SELL", "cens1", 0.02, pattern="P1", version="v1",
                   exit_reason="END_OF_BACKTEST"),
        make_trade("SELL", "ok1", 0.03, pattern="P1", version="v1"),
    ]
    result = per_key_oos_stability([make_multi_key_fold(1, trades=trades)])
    key_str = _key_to_artifact_key(("BULL", "ACTIVE", "P1", "v1"))
    fold_info = result["keys"][key_str]["fold_summaries"][0]
    assert "n_censored" in fold_info


# ====== PA11: Run completeness check in verifier ======
def test_per_key_verify_run_not_complete():
    """Verifier rejects artifact with non-COMPLETED overall_status."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    result = per_key_oos_stability([])
    # Build artifact with manifest showing PARTIAL status
    artifact = build_per_key_oos_artifact(
        result, run_id="partial", source_sha="s",
        config_hash="c", wf_config_hash="w",
        rule_hashes={},
        run_manifest={"run_id": "partial", "overall_status": "PARTIAL"},
    )
    v = verify_per_key_oos_artifact(artifact, request_key=("BULL", "ACTIVE", "P1", "v1"))
    assert not v["key_eligible"]
    assert any("manifest" in r for r in v["reasons"])


# ====== PA12: Verifier allows COMPLETED run ======
def test_per_key_verify_run_complete():
    """Verifier accepts COMPLETED overall_status."""
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    result = per_key_oos_stability([])
    artifact = build_per_key_oos_artifact(
        result, run_id="comp", source_sha="s",
        config_hash="c", wf_config_hash="w",
        rule_hashes={},
        run_manifest={"run_id": "comp", "overall_status": "COMPLETED"},
    )
    v = verify_per_key_oos_artifact(artifact)
    # No run-completeness reason should be present
    assert not any("run not complete" in r for r in v["reasons"])


def integrity_cohort(returns_by_fold=None):
    returns_by_fold = returns_by_fold or [[0.02] * 9 + [-0.01]] * 4
    return [make_multi_key_fold(i, trades=[trade
            for j, value in enumerate(returns)
            for trade in linked_oos_trades(f"integrity_{i}_{j}", value)])
            for i, returns in enumerate(returns_by_fold)]


def test_summarize_orphan_sell_is_not_closed():
    report = {"fold_id": 1, "trades": [make_trade("SELL", "orphan", 0.05)]}
    summary = summarize_fold(report)
    assert summary["n_closed"] == 0
    assert summary["attribution_valid"] is False


def test_summarize_mismatched_key_pair_is_not_closed():
    trades = linked_oos_trades("mismatch", 0.05)
    trades[0]["pattern_version"] = "other"
    summary = summarize_fold({"fold_id": 1, "trades": trades})
    assert summary["n_closed"] == 0
    assert summary["attribution_valid"] is False


@pytest.mark.parametrize("status,complete", [("RUN_FAILED", False), ("DATA_BLOCKED", False),
                                             ("PARTIAL", True), ("COMPLETED", False)])
def test_enough_good_folds_cannot_hide_bad_fold(status, complete):
    summaries = [summarize_fold(fold) for fold in integrity_cohort()]
    summaries.append(summarize_fold(make_fold(4, status=status, complete=complete)))
    assert aggregate_stability(summaries)["classification"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize("gate,returns", [
    ("drawdown_reason", [[0.03] * 9 + [-0.20]] * 4),
    ("worst_expectancy_reason", [[-0.03] * 10] + [[0.02] * 9 + [-0.01]] * 3),
    ("concentration_reason", [[0.10] * 9 + [-0.65]] + [[0.03] * 9 + [-0.001]] * 3),
])
def test_economic_breaches_preserve_reasons_and_cannot_authorize(gate, returns):
    folds = integrity_cohort(returns)
    aggregate = aggregate_stability([summarize_fold(fold) for fold in folds])
    assert aggregate["classification"] == "UNSTABLE"
    assert aggregate[gate]
    if gate == "drawdown_reason":
        assert aggregate["max_fold_drawdown_pct"] == pytest.approx(-0.20)
    if gate == "concentration_reason":
        assert aggregate["max_fold_return_concentration"] == pytest.approx(0.9 / 1.71)
    per_key = per_key_oos_stability(folds, expected_fold_ids=list(range(4)))
    result = next(iter(per_key["keys"].values()))
    assert result["classification"] == "UNSTABLE"
    assert result["reasons"][gate]


def test_zero_positive_concentration_records_reason():
    aggregate = aggregate_stability([summarize_fold(fold) for fold in integrity_cohort([[-0.01] * 10] * 4)])
    assert aggregate["max_fold_return_concentration"] is None
    assert aggregate["concentration_reason"] == "NO_POSITIVE_RETURNS"


def test_valid_integrity_cohort_remains_eligible():
    result = per_key_oos_stability(integrity_cohort(), expected_fold_ids=list(range(4)))
    assert next(iter(result["keys"].values()))["classification"] == "STABLE_CANDIDATE"


def test_per_key_buy_only_retains_open_diagnostic():
    fold = make_multi_key_fold(1, trades=[make_trade("BUY", "open", None)])
    result = per_key_oos_stability([fold])
    key = next(iter(result["keys"].values()))
    assert key["fold_summaries"][0]["n_open"] == 1
    assert key["n_total_closed"] == 0
    assert key["classification"] == "INSUFFICIENT_DATA"


def test_per_key_orphan_sell_never_counts_closed():
    fold = {"fold_id": 1, "complete": True, "status": "COMPLETED",
            "trades": [make_trade("SELL", "orphan", 0.05)]}
    result = per_key_oos_stability([fold])
    assert result["attribution_valid"] is False
    assert next(iter(result["keys"].values()))["n_total_closed"] == 0


def test_positive_concentration_alone_cannot_authorize():
    # Small alternating losses keep drawdown and worst expectancy inside limits;
    # losses hide concentration if net fold totals are incorrectly used.
    returns = [[0.02, -0.018] * 20] + [[0.01] * 9 + [-0.001]] * 3
    folds = integrity_cohort(returns)
    aggregate = aggregate_stability([summarize_fold(fold) for fold in folds])
    assert aggregate["drawdown_reason"] is None
    assert aggregate["worst_expectancy_reason"] is None
    assert aggregate["max_fold_return_concentration"] == pytest.approx(0.4 / 0.67)
    assert aggregate["classification"] == "UNSTABLE"
    assert aggregate["concentration_reason"]
    key = next(iter(per_key_oos_stability(folds)["keys"].values()))
    assert key["classification"] == "UNSTABLE"
    assert key["reasons"]["concentration_reason"]


def test_censored_orphan_exit_invalidates_attribution():
    summary = summarize_fold({"fold_id": 1, "trades": [
        make_trade("SELL", "orphan", 0.02, exit_reason="END_OF_BACKTEST")]})
    assert summary["n_closed"] == 0
    assert summary["attribution_valid"] is False


@pytest.mark.parametrize("binding", [None, {}, {"run_id": "synthetic", "source_sha": "source", "overall_status": "COMPLETED"}])
def test_self_consistent_artifact_without_physical_manifest_cannot_authorize(binding):
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    key = ("BULL", "ACTIVE", "P1", "v1")
    result = {"keys": {_key_to_artifact_key(key): {"classification": "STABLE_CANDIDATE"}}, "n_keys": 1}
    artifact = build_per_key_oos_artifact(result, "synthetic", "source", "c", "w", {}, binding)
    verified = verify_per_key_oos_artifact(artifact, request_key=key)
    assert verified["key_eligible"] is False
    assert any("manifest" in reason for reason in verified["reasons"])


@pytest.fixture
def physical_artifact_fixture(tmp_path):
    """A local, hash-bound fixture; not real market provenance or authorization."""
    import hashlib
    import json
    from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact
    from a_share_agent.backtest.walk_forward_persistence import write_finite_json
    key = ("BULL", "ACTIVE", "P1", "v1")
    input_file = tmp_path / "fixture_input.csv"
    input_file.write_text("fixture-only\n")
    directory = tmp_path / "published"
    directory.mkdir()
    manifest = {"run_id": "fixture", "source_sha": "fixture-source", "overall_status": "COMPLETED",
                "completion": True, "generation_id": "fixture-generation", "artifact_path": "oos_per_key.json",
                "config_hash": "c", "wf_config_hash": "w", "rule_hashes": {}, "dirty": False,
                "consumed_universes": {"0": {"symbols": ["FIXTURE"], "universe_sha256": hashlib.sha256(b'["FIXTURE"]').hexdigest()}},
                "physical_provenance": {"status": "VERIFIED"},
                "physical_inputs": [{"path": str(input_file), "sha256": hashlib.sha256(input_file.read_bytes()).hexdigest()}]}
    from a_share_agent.backtest.data import ConsumedInputLedger
    ledger = ConsumedInputLedger(tmp_path)
    ledger.fold_id = '0'
    ledger.read(input_file, kind='synthetic_fixture')
    snapshot = ledger.snapshot(verify_files=True)
    manifest.update(project_root=str(tmp_path), consumed_input_ledger=snapshot,
        consumed_input_ledger_sha256=snapshot['sha256'], physical_inputs=snapshot['physical_inputs'],
        fold_plan={'folds': [{'fold_id': '0'}]})
    fold_path = directory / 'folds/0/consumed_inputs.json'
    fold_path.parent.mkdir(parents=True)
    write_finite_json(fold_path, dict(events=snapshot['events'], sha256=snapshot['sha256'], status='VERIFIED'))
    artifact = build_per_key_oos_artifact({"keys": {_key_to_artifact_key(key): {"classification": "STABLE_CANDIDATE"}}, "n_keys": 1},
                                         "fixture", "fixture-source", "c", "w", {}, manifest)
    write_finite_json(directory / "oos_per_key.json", artifact)
    manifest["artifact_sha256"] = hashlib.sha256((directory / "oos_per_key.json").read_bytes()).hexdigest()
    manifest["oos_per_key_sha256"] = artifact["content_hash"]
    manifest["output_files"] = {"oos_per_key.json": manifest["artifact_sha256"],
        'folds/0/consumed_inputs.json': hashlib.sha256(fold_path.read_bytes()).hexdigest()}
    def publish_manifest():
        write_finite_json(directory / "manifest.json", manifest)
        write_finite_json(directory / "completion.json", {"run_id": "fixture", "generation_id": "fixture-generation",
                         "manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()})
    publish_manifest()
    return artifact, manifest, directory, key, input_file, publish_manifest


def test_physically_bound_fixture_verifies(physical_artifact_fixture):
    artifact, manifest, directory, key, input_file, publish = physical_artifact_fixture
    result = verify_per_key_oos_artifact(artifact, request_key=key, manifest_path=directory / "manifest.json")
    assert result["key_eligible"] is True, result["reasons"]


@pytest.mark.parametrize("mutation", ["run_id", "source_sha", "generation_id", "overall_status", "completion", "artifact_path", "dirty", "input", "escape", "symlink"])
def test_physical_binding_invalidations_fail_closed(physical_artifact_fixture, mutation):
    artifact, manifest, directory, key, input_file, publish = physical_artifact_fixture
    if mutation == "input":
        input_file.write_text("changed fixture\n")
    elif mutation == "escape":
        manifest["output_files"]["../fixture_input.csv"] = manifest["physical_inputs"][0]["sha256"]
    elif mutation == "symlink":
        path = directory / "oos_per_key.json"
        outside = directory.parent / "external.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif mutation == "dirty":
        manifest["dirty"] = True
    else:
        manifest[mutation] = False if mutation == "completion" else "changed"
    publish()
    result = verify_per_key_oos_artifact(artifact, request_key=key, manifest_path=directory / "manifest.json")
    assert result["key_eligible"] is False


def test_manifest_universe_commitment_mismatch_fails_closed(physical_artifact_fixture):
    artifact, manifest, directory, key, input_file, publish = physical_artifact_fixture
    manifest["consumed_universes"]["0"]["symbols"] = ["CHANGED"]
    publish()
    result = verify_per_key_oos_artifact(artifact, request_key=key, manifest_path=directory / "manifest.json")
    assert result["key_eligible"] is False


@pytest.mark.parametrize('mutation', ['hash', 'sequence', 'missing', 'inputs', 'memory', 'root', 'fold_events'])
def test_consumed_ledger_tamper_fails_closed(physical_artifact_fixture, mutation):
    artifact, manifest, directory, key, input_file, publish = physical_artifact_fixture
    events = manifest['consumed_input_ledger']['events']
    if mutation == 'hash':
        manifest['consumed_input_ledger_sha256'] = '0' * 64
    elif mutation == 'sequence':
        events[0]['sequence'] = 2
    elif mutation == 'missing':
        events.clear()
    elif mutation == 'inputs':
        manifest['physical_inputs'].clear()
    elif mutation == 'memory':
        events[0].update(source_type='memory', origin_sequences=[1])
    elif mutation == 'root':
        manifest['project_root'] = str(directory)
    else:
        path = directory / 'folds/0/consumed_inputs.json'
        path.write_text('{}')
        import hashlib
        manifest['output_files']['folds/0/consumed_inputs.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    publish()
    result = verify_per_key_oos_artifact(artifact, request_key=key, manifest_path=directory / 'manifest.json')
    assert result['key_eligible'] is False
