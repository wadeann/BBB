"""OOS stability statistics: hand-computed behavioral tests.

All pnl_pct values are fractions (0.05 = 5%). Trades use production contract
fields: direction (BUY/SELL), round_trip_id, exit_reason, quality state dicts
with state="ok". END_OF_BACKTEST exits excluded from closed stats. Tests
notify Main WAIT red before production ran — these verify the contract.
"""
import json
import pytest
from a_share_agent.backtest.oos_stability import summarize_fold, aggregate_stability


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
    f1 = make_fold(1, [make_trade("SELL", f"t1_{j}", 0.10) for j in range(10)])
    folds = [f1] + [make_fold(i, [make_trade("SELL", f"t{i}_{j}", 0.01) for j in range(10)]) for i in range(2, 5)]
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"
    assert agg["max_fold_return_concentration"] == pytest.approx(1.0 / 1.30)


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

# ====== 15. Concentration ======
def test_concentrated_single_fold_return():
    folds = []
    for i in range(4):
        if i == 0:
            trades = [make_trade("SELL", f"t{i}_big", 0.60)] + [make_trade("SELL", f"t{i}_{j}", 0.02) for j in range(9)] + [make_trade("SELL", f"t{i}_{j}_l", -0.01) for j in range(1)]
        else:
            trades = [make_trade("SELL", f"t{i}_{j}", 0.02) for j in range(9)] + [make_trade("SELL", f"t{i}_{j}_l", -0.01) for j in range(1)]
        folds.append(make_fold(i, trades))
    agg = aggregate_stability([summarize_fold(f) for f in folds])
    assert agg["classification"] == "UNSTABLE"
    assert agg["max_fold_return_concentration"] is not None
