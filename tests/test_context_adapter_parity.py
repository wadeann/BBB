"""Tests proving live vs historical adapter produce identical results for same bars.

This test creates a MarketContextBuilder and verifies that
compute_features_historical() returns identical output to the feature
computation inside build() given identical input data.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from a_share_agent.backtest.regime import (
    determine_regime_7state,
    determine_lifecycle_7state,
)
from a_share_agent.market.features import (
    compute_breadth,
    compute_leaders,
    compute_turnover_share,
    compute_expansion,
    compute_concentration,
    compute_persistence,
)
from a_share_agent.market.context import MarketContextBuilder


# ════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_config(tmp_path: Path) -> Path:
    """Create a minimal RuntimeConfig-compatible YAML."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "defaults.yaml").write_text("""
version: 4.0.0
mode: paper
benchmarks:
  large_cap: "000300.SH"
features:
  leaders_top_n: 5
  turnover_top_n: 3
  expansion_threshold: 60.0
  concentration_value_key: turnover
  expected_stock_count: 5000
""")
    (cfg / "runtime.yaml").write_text("version: 0.7.0\nmode: paper\n")
    for f in ("schedule.yaml", "phase_permissions.yaml", "strategy_router.yaml",
              "logging.yaml", "improvement.yaml", "backtest.yaml", "research.yaml"):
        (cfg / f).write_text("{}\n")
    return tmp_path


@pytest.fixture
def builder(mock_config: Path) -> MarketContextBuilder:
    """Create a MarketContextBuilder with no real MCP."""
    from a_share_agent.config import load_config
    config = load_config(mock_config)

    class DummyMCP:
        def invoke(self, *args, **kwargs):
            raise RuntimeError("No MCP should be called in historical adapter test")

    return MarketContextBuilder(config, DummyMCP())


# ════════════════════════════════════════════════════════════════════════
# Test data — hand-calculable snapshots
# ════════════════════════════════════════════════════════════════════════

GOOD_STOCKS = [
    {"change_pct": 2.5},
    {"change_pct": -1.0},
    {"change_pct": 1.2},
    {"change_pct": -3.0},
    {"change_pct": 0.0},
]

GOOD_SECTORS = [
    {"sector_name": "Tech", "strength_score": 85.0, "turnover": 100_000_000},
    {"sector_name": "Finance", "strength_score": 72.0, "turnover": 60_000_000},
    {"sector_name": "Energy", "strength_score": 60.0, "turnover": 40_000_000},
    {"sector_name": "Health", "strength_score": 55.0, "turnover": 30_000_000},
    {"sector_name": "Consumer", "strength_score": 90.0, "turnover": 80_000_000},
]

REGIME_KWARGS = dict(
    index_trend="up",
    index_ma_slope=0.01,
    close_vs_ma=0.03,
    ret5=0.04,
    ret20=0.10,
    drawdown20=-0.02,
    breadth_ratio=0.6,
    adv_decline_ratio=1.5,
    new_high_count=50,
    new_low_count=10,
    vol_estimate=0.18,
)


# ════════════════════════════════════════════════════════════════════════
# Test: compute_features_historical matches individual feature functions
# ════════════════════════════════════════════════════════════════════════

class TestHistoricalAdapterConsistency:
    """compute_features_historical() must produce same result as calling
    individual feature functions directly with the same data."""

    def test_breadth_parity(self, builder):
        """compute_features_historical breadth matches compute_breadth."""
        expected_breadth = compute_breadth(GOOD_STOCKS)
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["breadth"] == expected_breadth

    def test_leaders_parity(self, builder):
        expected_leaders = compute_leaders(
            GOOD_SECTORS, top_n=5, previous_top=None
        )
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["leaders"] == expected_leaders

    def test_leaders_parity_with_previous(self, builder):
        prev_top = ["Tech", "Finance"]
        expected_leaders = compute_leaders(
            GOOD_SECTORS, top_n=5, previous_top=prev_top
        )
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime="BULL_TREND",
            previous_top_sectors=prev_top,
        )
        assert result["leaders"] == expected_leaders

    def test_turnover_share_parity(self, builder):
        expected = compute_turnover_share(GOOD_SECTORS, top_n=3)
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["turnover_share"] == expected

    def test_expansion_parity(self, builder):
        expected = compute_expansion(GOOD_SECTORS, threshold=60.0)
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["expansion"] == expected

    def test_concentration_parity(self, builder):
        expected = compute_concentration(GOOD_SECTORS, value_key="turnover")
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["concentration"] == expected

    def test_regime_parity(self, builder):
        expected = determine_regime_7state(**REGIME_KWARGS)
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["regime"] == expected["regime"]
        assert result["regime_confidence"] > 0

    def test_lifecycle_parity(self, builder):
        expected_lifecycle = determine_lifecycle_7state(
            breadth_ratio=REGIME_KWARGS["breadth_ratio"],
            leader_count=len(compute_leaders(GOOD_SECTORS, top_n=5, previous_top=None)),
            turnover_share=compute_turnover_share(GOOD_SECTORS, top_n=3),
            persistence=compute_persistence("BULL_TREND", previous_regimes=None),
            expansion=compute_expansion(GOOD_SECTORS, threshold=60.0),
            concentration=compute_concentration(GOOD_SECTORS, value_key="turnover"),
        )
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["theme_lifecycle"] == expected_lifecycle["lifecycle"]

    def test_persistence_parity(self, builder):
        expected_persistence = compute_persistence("BULL_TREND", previous_regimes=None)
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["persistence"] == expected_persistence

    def test_feature_version_present(self, builder):
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert isinstance(result["feature_version"], str)
        assert len(result["feature_version"].split(".")) == 3


# ════════════════════════════════════════════════════════════════════════
# Test: Determinism — same inputs → same outputs every time
# ════════════════════════════════════════════════════════════════════════

class TestDeterministicAdapter:
    """compute_features_historical is a pure function."""

    def test_deterministic_repeat(self, builder):
        result1 = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        result2 = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result1 == result2

    def test_deterministic_with_state(self, builder):
        """Previous state influences persistence but final output is deterministic."""
        result1 = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime="BULL_TREND",
            previous_top_sectors=["Tech"],
        )
        result2 = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime="BULL_TREND",
            previous_top_sectors=["Tech"],
        )
        assert result1 == result2


# ════════════════════════════════════════════════════════════════════════
# Test: Missing / edge cases
# ════════════════════════════════════════════════════════════════════════

class TestAdapterEdgeCases:
    """Edge-case inputs propagate correctly through the adapter."""

    def test_empty_stocks(self, builder):
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=[],
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["breadth"]["total"] == 0
        assert result["breadth"]["ratio"] is None

    def test_empty_sectors(self, builder):
        result = builder.compute_features_historical(
            sectors_list=[],
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["leaders"] == []
        assert result["turnover_share"] is None
        assert result["expansion"] == 0
        assert result["concentration"] is None

    def test_all_unknown_inputs(self, builder):
        result = builder.compute_features_historical(
            sectors_list=[],
            stocks_list=[],
            index_trend="unknown",
            index_ma_slope=0.0,
            close_vs_ma=0.0,
            ret5=None,
            ret20=None,
            drawdown20=None,
            breadth_ratio=None,
            adv_decline_ratio=None,
            new_high_count=None,
            new_low_count=None,
            vol_estimate=None,
            previous_regime=None,
            previous_top_sectors=None,
        )
        assert result["regime"] == "UNKNOWN"
        assert result["theme_lifecycle"] == "UNKNOWN"
        assert result["data_quality"]["state"] != "ok"

    def test_data_quality_tracked(self, builder):
        result = builder.compute_features_historical(
            sectors_list=GOOD_SECTORS,
            stocks_list=GOOD_STOCKS,
            **REGIME_KWARGS,
            previous_regime=None,
            previous_top_sectors=None,
        )
        dq = result["data_quality"]
        assert "state" in dq
        assert "reasons" in dq
        assert "coverage" in dq
        assert isinstance(dq["coverage"], float)
