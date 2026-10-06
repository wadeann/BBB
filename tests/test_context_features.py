"""Tests for pure deterministic feature computation and 7-state regime/lifecycle.

C01: 7-state regime + 7-state lifecycle all deterministic; same as-of → same result
C02: Breadth denominator = only day's eligible stocks; IPO/delisted/suspended produce explicit coverage
C03: Hand-calculable fixture for each feature; zero-denom and insufficient-sample edges
C04: Missing required data → UNKNOWN/degraded; confidence ≠ quality
C05: No future sector/leader/turnover leakage; entry Context snapshot not overwritten
C06: Feature version tracked; can re-run with versioned identity
"""
from __future__ import annotations

from statistics import mean

import pytest

from a_share_agent.backtest.regime import (
    REGIME_7STATE,
    LIFECYCLE_7STATE,
    determine_regime_7state,
    determine_lifecycle_7state,
    compute_regime_confidence,
    compute_data_quality,
)
from a_share_agent.market.features import (
    FEATURE_VERSION,
    compute_breadth,
    compute_leaders,
    compute_turnover_share,
    compute_persistence,
    compute_expansion,
    compute_concentration,
)


# ════════════════════════════════════════════════════════════════════════
# C01: Determinism — same inputs → same outputs
# ════════════════════════════════════════════════════════════════════════

class TestDeterminism:
    """Every function is a pure function of its arguments."""

    def test_breadth_deterministic(self):
        stocks = [{"change_pct": 1.0}, {"change_pct": -0.5}, {"change_pct": 0.3}, {"change_pct": -1.2}]
        r1 = compute_breadth(stocks)
        r2 = compute_breadth(stocks)
        assert r1 == r2

    def test_regime_deterministic(self):
        kwargs = dict(
            index_trend="up", index_ma_slope=0.01, close_vs_ma=0.03,
            ret5=0.04, ret20=0.10, drawdown20=-0.02,
            breadth_ratio=0.62, adv_decline_ratio=1.6,
            new_high_count=50, new_low_count=5, vol_estimate=0.18,
        )
        r1 = determine_regime_7state(**kwargs)
        r2 = determine_regime_7state(**kwargs)
        assert r1 == r2

    def test_lifecycle_deterministic(self):
        kwargs = dict(
            breadth_ratio=0.65, leader_count=4, turnover_share=0.35,
            persistence=8, expansion=6, concentration=0.12,
        )
        r1 = determine_lifecycle_7state(**kwargs)
        r2 = determine_lifecycle_7state(**kwargs)
        assert r1 == r2

    def test_feature_version_tracked(self):
        assert isinstance(FEATURE_VERSION, str)
        assert len(FEATURE_VERSION.split(".")) == 3  # semver


# ════════════════════════════════════════════════════════════════════════
# C02: Breadth — eligible stocks only
# ════════════════════════════════════════════════════════════════════════

class TestBreadth:
    """Breadth uses only day's eligible stocks."""

    def test_basic_breadth(self):
        stocks = [
            {"change_pct": 2.5},  # advancing
            {"change_pct": -1.0},  # declining
            {"change_pct": 0.0},  # flat — neither
            {"change_pct": 1.2},  # advancing
            {"change_pct": -3.0},  # declining
        ]
        result = compute_breadth(stocks)
        assert result["advancing"] == 2
        assert result["declining"] == 2
        assert result["total"] == 5
        assert result["ratio"] == pytest.approx(0.5)
        assert result["eligible_count"] == 5
        assert result["audit_trail"]["total_universe"] == 5
        assert result["audit_trail"]["filtered_out_ipo"] == 0
        assert result["audit_trail"]["filtered_out_delisted"] == 0
        assert result["audit_trail"]["filtered_out_suspended"] == 0

    def test_breadth_ratio_none_on_zero_denom(self):
        stocks = [{"change_pct": 0.0}, {"change_pct": 0.0}]
        result = compute_breadth(stocks)
        assert result["advancing"] == 0
        assert result["declining"] == 0
        assert result["ratio"] is None

    def test_breadth_handles_missing_change(self):
        stocks = [
            {"change_pct": 1.0},
            {},  # no change_pct
            {"change_pct": None},
        ]
        result = compute_breadth(stocks)
        assert result["advancing"] == 1
        assert result["declining"] == 0
        assert result["total"] == 3

    def test_breadth_all_advancing(self):
        stocks = [{"change_pct": 0.5}, {"change_pct": 1.2}, {"change_pct": 3.0}]
        result = compute_breadth(stocks)
        assert result["advancing"] == 3
        assert result["declining"] == 0
        assert result["ratio"] == 1.0

    def test_breadth_audit_trail_custom_filters(self):
        stocks = [{"change_pct": 0.5}, {"change_pct": 1.2}]
        result = compute_breadth(
            stocks,
            total_universe=100,
            filtered_out_ipo=5,
            filtered_out_delisted=3,
            filtered_out_suspended=2,
        )
        assert result["eligible_count"] == 2
        assert result["audit_trail"]["total_universe"] == 100
        assert result["audit_trail"]["filtered_out_ipo"] == 5
        assert result["audit_trail"]["filtered_out_delisted"] == 3
        assert result["audit_trail"]["filtered_out_suspended"] == 2


# ════════════════════════════════════════════════════════════════════════
# C03: Hand-calculable fixtures
# ════════════════════════════════════════════════════════════════════════

class TestLeaders:
    """Hand-calculable leader selection with persistence."""

    def test_top_n_sectors(self):
        sectors = [
            {"sector_name": "Tech", "strength_score": 85.0},
            {"sector_name": "Finance", "strength_score": 72.0},
            {"sector_name": "Energy", "strength_score": 60.0},
            {"sector_name": "Health", "strength_score": 55.0},
            {"sector_name": "Consumer", "strength_score": 90.0},
            {"sector_name": "Materials", "strength_score": 40.0},
        ]
        result = compute_leaders(sectors, top_n=3, previous_top=None)
        assert len(result) == 3
        assert result[0]["sector"] == "Consumer"  # strength 90
        assert result[1]["sector"] == "Tech"      # strength 85
        assert result[2]["sector"] == "Finance"   # strength 72
        assert result[0]["rank"] == 1
        # persistence defaults to 1 when no previous_top
        assert result[0]["persistence"] == 1
        assert result[2]["persistence"] == 1

    def test_leaders_persistence(self):
        sectors = [
            {"sector_name": "Tech", "strength_score": 85.0},
            {"sector_name": "Finance", "strength_score": 72.0},
            {"sector_name": "Energy", "strength_score": 60.0},
        ]
        result = compute_leaders(sectors, top_n=2, previous_top=["Tech", "Finance"])
        assert result[0]["sector"] == "Tech"
        assert result[0]["persistence"] == 2  # was in previous top
        assert result[1]["sector"] == "Finance"
        assert result[1]["persistence"] == 2

    def test_leaders_empty_sectors(self):
        assert compute_leaders([], top_n=3) == []

    def test_leaders_missing_scores_skipped(self):
        sectors = [
            {"sector_name": "Tech", "strength_score": None},
            {"sector_name": "Finance", "strength_score": 72.0},
        ]
        result = compute_leaders(sectors, top_n=3)
        assert len(result) == 1
        assert result[0]["sector"] == "Finance"


class TestTurnoverShare:
    """Hand-calculable turnover concentration."""

    def test_top_3_share(self):
        sectors = [
            {"sector_name": "A", "turnover": 100_000_000},
            {"sector_name": "B", "turnover": 60_000_000},
            {"sector_name": "C", "turnover": 40_000_000},
            {"sector_name": "D", "turnover": 30_000_000},
        ]
        result = compute_turnover_share(sectors, top_n=3)
        # top 3 = 100+60+40 = 200; total = 230; share = 200/230 ≈ 0.869565; rounded to 4dp = 0.8696
        assert result == 0.8696

    def test_turnover_share_none_on_zero(self):
        sectors = [{"sector_name": "A", "turnover": 0}]
        assert compute_turnover_share(sectors) is None

    def test_turnover_share_empty(self):
        assert compute_turnover_share([]) is None

    def test_turnover_share_missing_values_skipped(self):
        sectors = [
            {"sector_name": "A", "turnover": 100},
            {"sector_name": "B"},  # no turnover
        ]
        result = compute_turnover_share(sectors)
        assert result == 1.0  # only A contributes


class TestPersistence:
    """Consecutive same-regime counting."""

    def test_first_day(self):
        assert compute_persistence("BULL_TREND", previous_regimes=None) == 1

    def test_two_days_same(self):
        assert compute_persistence("BULL_TREND", previous_regimes="BULL_TREND") == 2

    def test_two_days_different(self):
        assert compute_persistence("BULL_TREND", previous_regimes="SIDEWAYS") == 1

    def test_list_consecutive(self):
        prev = ["BULL_TREND", "BULL_TREND", "SIDEWAYS"]
        assert compute_persistence("BULL_TREND", previous_regimes=prev) == 3

    def test_list_break(self):
        # previous_regimes are most-recent-first; first entry matches → count=2
        prev = ["BULL_TREND", "SIDEWAYS"]
        assert compute_persistence("BULL_TREND", previous_regimes=prev) == 2


class TestExpansion:
    """Sector count above threshold."""

    def test_expansion_count(self):
        sectors = [
            {"sector_name": "A", "strength_score": 80},
            {"sector_name": "B", "strength_score": 65},
            {"sector_name": "C", "strength_score": 55},
            {"sector_name": "D", "strength_score": 70},
        ]
        assert compute_expansion(sectors, threshold=60.0) == 3  # A, B, D

    def test_expansion_none_above(self):
        sectors = [{"sector_name": "A", "strength_score": 30}]
        assert compute_expansion(sectors, threshold=60.0) == 0

    def test_expansion_empty(self):
        assert compute_expansion([], threshold=60.0) == 0

    def test_expansion_handles_none_score(self):
        sectors = [{"sector_name": "A", "strength_score": None}]
        assert compute_expansion(sectors, threshold=60.0) == 0


class TestConcentration:
    """Herfindahl-Hirschman index."""

    def test_concentration_hand_calculable(self):
        sectors = [
            {"sector_name": "A", "turnover": 50},
            {"sector_name": "B", "turnover": 30},
            {"sector_name": "C", "turnover": 20},
        ]
        total = 100
        expected = (50 / total) ** 2 + (30 / total) ** 2 + (20 / total) ** 2
        result = compute_concentration(sectors, value_key="turnover")
        assert result == pytest.approx(expected)

    def test_concentration_monopoly(self):
        sectors = [{"sector_name": "A", "turnover": 100}]
        assert compute_concentration(sectors) == pytest.approx(1.0)

    def test_concentration_none_on_zero(self):
        sectors = [{"sector_name": "A", "turnover": 0}]
        assert compute_concentration(sectors) is None

    def test_concentration_empty(self):
        assert compute_concentration([]) is None


# ════════════════════════════════════════════════════════════════════════
# C04: Missing data → UNKNOWN/degraded; confidence ≠ quality
# ════════════════════════════════════════════════════════════════════════

class TestRegimeMissingData:
    """Missing data yields UNKNOWN regime."""

    def test_unknown_on_no_index(self):
        result = determine_regime_7state(
            index_trend="unknown", index_ma_slope=0.0, close_vs_ma=0.0,
            ret5=None, ret20=None, drawdown20=None,
            breadth_ratio=None, adv_decline_ratio=None,
            new_high_count=None, new_low_count=None, vol_estimate=None,
        )
        assert result["regime"] == "UNKNOWN"
        assert result["confidence"] == 0.0

    def test_unknown_on_no_ret(self):
        result = determine_regime_7state(
            index_trend="up", index_ma_slope=0.01, close_vs_ma=0.03,
            ret5=0.0, ret20=None, drawdown20=None,
            breadth_ratio=None, adv_decline_ratio=None,
            new_high_count=None, new_low_count=None, vol_estimate=None,
        )
        assert result["regime"] == "UNKNOWN"


class TestLifecycleMissingData:
    """Missing data yields UNKNOWN lifecycle."""

    def test_unknown_on_no_data(self):
        result = determine_lifecycle_7state(
            breadth_ratio=None, leader_count=None, turnover_share=None,
            persistence=1, expansion=None, concentration=None,
        )
        assert result["lifecycle"] == "UNKNOWN"
        assert result["confidence"] == 0.0


class TestConfidenceVsQuality:
    """Confidence and quality are distinct concepts."""

    def test_confidence_is_not_quality(self):
        # High-confidence regime with partial data coverage
        result = determine_regime_7state(
            index_trend="up", index_ma_slope=0.015, close_vs_ma=0.04,
            ret5=0.05, ret20=0.12, drawdown20=-0.02,
            breadth_ratio=None, adv_decline_ratio=None,
            new_high_count=None, new_low_count=None, vol_estimate=None,
        )
        assert result["regime"] == "BULL_TREND"
        assert result["confidence"] > 0  # high confidence from price alone
        # Quality = separate dimension
        quality = compute_data_quality(
            has_index_data=True,
            breadth_coverage=0.0,
            has_sector_data=False,
        )
        assert quality["state"] != "ok"

    def test_data_quality_degraded(self):
        quality = compute_data_quality(
            has_index_data=True,
            breadth_coverage=0.5,
            has_sector_data=False,
        )
        assert quality["state"] in ("degraded", "unknown")
        assert quality["coverage"] < 0.9

    def test_data_quality_unknown(self):
        quality = compute_data_quality(has_index_data=False)
        assert quality["state"] == "unknown"
        assert quality["coverage"] == 0.0

    def test_data_quality_ok(self):
        quality = compute_data_quality(
            has_index_data=True,
            breadth_coverage=1.0,
            has_sector_data=True,
        )
        assert quality["state"] == "ok"
        assert quality["coverage"] == 1.0


# ════════════════════════════════════════════════════════════════════════
# C05: No future leakage
# ════════════════════════════════════════════════════════════════════════

class TestNoFutureLeakage:
    """Features computed from current snapshot only."""

    def test_persistence_uses_only_past(self):
        """Persistence counts backwards from current, never forward."""
        # current=SIDEWAYS, past=[SIDEWAYS, BULL_TREND] → count=2
        assert compute_persistence("SIDEWAYS", previous_regimes=["SIDEWAYS", "BULL_TREND"]) == 2

    def test_leaders_uses_only_previous_top(self):
        """Leader persistence counts from previous day's top, not future."""
        sectors = [{"sector_name": "Tech", "strength_score": 90}]
        result = compute_leaders(sectors, top_n=1, previous_top=["Tech"])
        assert result[0]["persistence"] == 2  # 1 current + 1 from past


# ════════════════════════════════════════════════════════════════════════
# C06: Feature version tracked
# ════════════════════════════════════════════════════════════════════════

class TestFeatureVersion:
    """Feature version is stable and comparable."""

    def test_version_is_string(self):
        assert isinstance(FEATURE_VERSION, str)

    def test_version_is_semver(self):
        parts = FEATURE_VERSION.split(".")
        assert len(parts) == 3
        for p in parts:
            assert p.isdigit()

    def test_regime_state_set_complete(self):
        assert REGIME_7STATE == frozenset({
            "BULL_TREND", "BULL_VOLATILE", "ROTATION", "SIDEWAYS",
            "BEAR", "PANIC", "RECOVERY",
        })

    def test_lifecycle_state_set_complete(self):
        assert LIFECYCLE_7STATE == frozenset({
            "EMERGING", "ACCELERATING", "LEADING", "MATURE",
            "DISTRIBUTING", "FADING", "UNKNOWN",
        })


# ════════════════════════════════════════════════════════════════════════
# 7-state regime scenarios
# ════════════════════════════════════════════════════════════════════════

class TestRegime7State:
    """Each 7-state regime is reachable via deterministic inputs."""

    def test_bull_trend(self):
        result = determine_regime_7state(
            index_trend="up", index_ma_slope=0.02, close_vs_ma=0.05,
            ret5=0.04, ret20=0.12, drawdown20=-0.02,
            breadth_ratio=0.65, adv_decline_ratio=1.8,
            new_high_count=80, new_low_count=5, vol_estimate=0.15,
        )
        assert result["regime"] == "BULL_TREND"
        assert result["confidence"] > 0.5

    def test_bull_volatile(self):
        result = determine_regime_7state(
            index_trend="up", index_ma_slope=0.01, close_vs_ma=0.02,
            ret5=0.02, ret20=0.06, drawdown20=-0.05,
            breadth_ratio=0.55, adv_decline_ratio=1.2,
            new_high_count=30, new_low_count=20, vol_estimate=0.28,
        )
        assert result["regime"] == "BULL_VOLATILE"
        assert result["confidence"] > 0.5

    def test_rotation(self):
        result = determine_regime_7state(
            index_trend="range", index_ma_slope=0.0, close_vs_ma=0.0,
            ret5=-0.01, ret20=0.04, drawdown20=-0.03,
            breadth_ratio=0.50, adv_decline_ratio=1.0,
            new_high_count=25, new_low_count=25, vol_estimate=0.20,
        )
        assert result["regime"] == "ROTATION"

    def test_sideways(self):
        result = determine_regime_7state(
            index_trend="range", index_ma_slope=0.001, close_vs_ma=0.005,
            ret5=0.005, ret20=0.01, drawdown20=-0.02,
            breadth_ratio=0.52, adv_decline_ratio=1.1,
            new_high_count=30, new_low_count=20, vol_estimate=0.15,
        )
        assert result["regime"] == "SIDEWAYS"

    def test_bear(self):
        result = determine_regime_7state(
            index_trend="down", index_ma_slope=-0.01, close_vs_ma=-0.04,
            ret5=-0.03, ret20=-0.07, drawdown20=-0.09,
            breadth_ratio=0.35, adv_decline_ratio=0.5,
            new_high_count=5, new_low_count=80, vol_estimate=0.25,
        )
        assert result["regime"] == "BEAR"
        assert result["confidence"] > 0.5

    def test_panic(self):
        result = determine_regime_7state(
            index_trend="down", index_ma_slope=-0.02, close_vs_ma=-0.08,
            ret5=-0.08, ret20=-0.12, drawdown20=-0.15,
            breadth_ratio=0.25, adv_decline_ratio=0.3,
            new_high_count=2, new_low_count=120, vol_estimate=0.40,
        )
        assert result["regime"] == "PANIC"
        assert result["confidence"] > 0.5

    def test_recovery(self):
        result = determine_regime_7state(
            index_trend="range", index_ma_slope=0.005, close_vs_ma=-0.01,
            ret5=0.04, ret20=-0.02, drawdown20=-0.10,
            breadth_ratio=0.58, adv_decline_ratio=1.4,
            new_high_count=40, new_low_count=10, vol_estimate=0.22,
        )
        assert result["regime"] == "RECOVERY"
        assert result["confidence"] > 0.5


# ════════════════════════════════════════════════════════════════════════
# 7-state lifecycle scenarios
# ════════════════════════════════════════════════════════════════════════

class TestLifecycle7State:
    """Each lifecycle state reachable via deterministic inputs."""

    def test_emerging(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.55, leader_count=2, turnover_share=0.25,
            persistence=3, expansion=3, concentration=0.20,
        )
        assert result["lifecycle"] == "EMERGING"

    def test_accelerating(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.62, leader_count=3, turnover_share=0.33,
            persistence=5, expansion=5, concentration=0.15,
        )
        assert result["lifecycle"] == "ACCELERATING"

    def test_leading(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.70, leader_count=5, turnover_share=0.38,
            persistence=12, expansion=8, concentration=0.12,
        )
        assert result["lifecycle"] == "LEADING"

    def test_mature(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.48, leader_count=1, turnover_share=0.28,
            persistence=3, expansion=2, concentration=0.20,
        )
        assert result["lifecycle"] == "MATURE"

    def test_distributing(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.42, leader_count=0, turnover_share=0.18,
            persistence=2, expansion=0, concentration=0.30,
        )
        assert result["lifecycle"] == "DISTRIBUTING"

    def test_fading(self):
        result = determine_lifecycle_7state(
            breadth_ratio=0.30, leader_count=0, turnover_share=0.15,
            persistence=2, expansion=0, concentration=0.30,
        )
        assert result["lifecycle"] == "FADING"

    def test_unknown(self):
        result = determine_lifecycle_7state(
            breadth_ratio=None, leader_count=None, turnover_share=None,
            persistence=1, expansion=None, concentration=None,
        )
        assert result["lifecycle"] == "UNKNOWN"


# ════════════════════════════════════════════════════════════════════════
# Edge cases — zero denominators, insufficient samples
# ════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Zero-denominator and insufficient-sample edges."""

    def test_breadth_zero_divisor(self):
        stocks = [{"change_pct": 0.0}, {"change_pct": 0.0}]
        result = compute_breadth(stocks)
        assert result["ratio"] is None  # no advancing or declining

    def test_turnover_zero_total(self):
        sectors = [{"sector_name": "A", "turnover": 0}]
        assert compute_turnover_share(sectors) is None

    def test_concentration_zero_total(self):
        sectors = [{"sector_name": "A", "turnover": 0}]
        assert compute_concentration(sectors) is None

    def test_persistence_minimum(self):
        assert compute_persistence("UNKNOWN", previous_regimes=None) == 1

    def test_expansion_empty_sectors(self):
        assert compute_expansion([]) == 0

    def test_leaders_top_n_more_than_available(self):
        sectors = [{"sector_name": "A", "strength_score": 80}]
        result = compute_leaders(sectors, top_n=5)
        assert len(result) == 1
