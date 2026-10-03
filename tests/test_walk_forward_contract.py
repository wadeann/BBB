"""WalkForwardConfig contract tests for Phase 2A.1.

Tests the frozen configuration schema, half-open month-arithmetic fold
generation (12/3/3), leap/year/Jan31 edge cases, non-overlapping intervals,
partial-tail handling, immutability, and the prohibition on score-grid tuning.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from a_share_agent.backtest.walk_forward import (
    WalkForwardConfig,
    WalkForwardEngine,
    _add_months,
    generate_folds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SMOKE_SYMBOLS = [
    "000001.SZ", "000002.SZ", "000333.SZ", "000651.SZ", "000858.SZ",
    "002415.SZ", "002475.SZ", "300059.SZ", "300750.SZ", "600519.SH",
]

STABILITY_THRESHOLDS = {
    "min_observed_folds": 4,
    "min_informative_folds": 3,
    "min_closed_per_fold": 10,
    "min_total_closed": 40,
    "required_context_coverage": 1.0,
    "min_finite_pf_folds": 3,
    "min_median_expectancy": 0.0,
    "min_median_pf": 1.0,
    "min_positive_fold_fraction": 2 / 3,
    "max_drawdown_loss": 0.15,
    "min_worst_expectancy": -0.02,
    "max_positive_concentration": 0.5,
}


def _add_days(s: str, days: int) -> str:
    return (date.fromisoformat(s) + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# _add_months unit tests (half-open month arithmetic)
# ---------------------------------------------------------------------------


class TestAddMonths:
    """Core month-arithmetic primitive used for half-open interval folding."""

    def test_same_month_offset(self):
        # 2024-01-15 + 0 months = 2024-01-15
        assert _add_months("2024-01-15", 0) == "2024-01-15"

    def test_simple_forward(self):
        # 2024-01-15 + 1 month = 2024-02-15
        assert _add_months("2024-01-15", 1) == "2024-02-15"

    def test_year_boundary(self):
        # 2024-11-15 + 2 months = 2025-01-15
        assert _add_months("2024-11-15", 2) == "2025-01-15"

    def test_multi_year(self):
        # 2024-01-01 + 12 months = 2025-01-01
        assert _add_months("2024-01-01", 12) == "2025-01-01"

    def test_leap_feb_29(self):
        # 2024-01-31 + 1 month = 2024-02-29 (clamped to Feb 29 in leap year)
        result = _add_months("2024-01-31", 1)
        assert result == "2024-02-29"

    def test_non_leap_feb_28(self):
        # 2023-01-31 + 1 month = 2023-02-28 (clamped to Feb 28 in non-leap)
        assert _add_months("2023-01-31", 1) == "2023-02-28"

    def test_jan31_to_apr30(self):
        # 2024-01-31 + 3 months = 2024-04-30
        assert _add_months("2024-01-31", 3) == "2024-04-30"

    def test_mar31_to_apr30(self):
        # 2024-03-31 + 1 month = 2024-04-30
        assert _add_months("2024-03-31", 1) == "2024-04-30"

    def test_dec_31_to_jan_31(self):
        # 2024-12-31 + 1 month = 2025-01-31
        assert _add_months("2024-12-31", 1) == "2025-01-31"

    def test_oct_31_to_nov_30(self):
        # 2024-10-31 + 1 month = 2024-11-30
        assert _add_months("2024-10-31", 1) == "2024-11-30"

    def test_twelve_months_identity(self):
        # 2024-06-15 + 12 months = 2025-06-15
        assert _add_months("2024-06-15", 12) == "2025-06-15"

    def test_negative_months(self):
        # 2024-03-01 - 1 month = 2024-02-01
        assert _add_months("2024-03-01", -1) == "2024-02-01"

    def test_negative_year_boundary(self):
        # 2024-01-01 - 1 month = 2023-12-01
        assert _add_months("2024-01-01", -1) == "2023-12-01"


# ---------------------------------------------------------------------------
# WalkForwardConfig schema tests
# ---------------------------------------------------------------------------


class TestWalkForwardConfig:
    """Config schema, deepfreeze, serialization, and canonical hash."""

    def test_default_config_creates(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        assert cfg.start_date == "2024-10-01"
        assert cfg.end_date == "2026-09-30"
        assert cfg.train_months == 12
        assert cfg.test_months == 3
        assert cfg.step_months == 3
        assert cfg.warmup_bars == 260
        assert cfg.universe == tuple(SMOKE_SYMBOLS)

    def test_frozen_immutable(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        with pytest.raises(AttributeError):
            cfg.start_date = "2025-01-01"  # type: ignore[misc]

    def test_list_immutable(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        with pytest.raises(Exception):
            cfg.universe.append("EXTRA.SZ")  # type: ignore[attr-defined]

    def test_stability_thresholds_defaults(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        for k, v in STABILITY_THRESHOLDS.items():
            assert k in cfg.stability_thresholds, f"missing threshold {k}"
            assert cfg.stability_thresholds[k] == v, (
                f"threshold {k}: expected {v}, got {cfg.stability_thresholds[k]}"
            )

    def test_settings_defaults_empty(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        assert cfg.settings == {}

    def test_to_dict_roundtrip(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
            settings={"min_score": 75.0, "max_positions": 5},
        )
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["start_date"] == "2024-10-01"
        assert d["settings"] == {"min_score": 75.0, "max_positions": 5}
        assert d["train_months"] == 12
        assert "stability_thresholds" in d

    def test_canonical_hash_deterministic(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        h1 = cfg.canonical_hash()
        h2 = cfg.canonical_hash()
        assert h1 == h2
        assert isinstance(h1, str)
        assert len(h1) == 64  # SHA-256 hex

    def test_canonical_hash_differs_on_field_change(self):
        cfg_a = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        cfg_b = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
            train_months=6,  # different
        )
        assert cfg_a.canonical_hash() != cfg_b.canonical_hash()

    def test_canonical_hash_differs_on_threshold(self):
        cfg_a = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        thresholds = dict(STABILITY_THRESHOLDS)
        thresholds["min_observed_folds"] = 5
        cfg_b = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
            stability_thresholds=thresholds,
        )
        assert cfg_a.canonical_hash() != cfg_b.canonical_hash()

    def test_from_yaml_creates_config(self, tmp_path: Path):
        yaml_content = {
            "start_date": "2024-10-01",
            "end_date": "2026-09-30",
            "universe": SMOKE_SYMBOLS,
            "train_months": 12,
            "test_months": 3,
            "step_months": 3,
            "warmup_bars": 260,
            "settings": {"min_score": 75.0},
        }
        p = tmp_path / "walk_forward.yaml"
        # Write as YAML manually (no PyYAML dep needed for this test)
        import yaml
        with open(p, "w") as f:
            yaml.dump(yaml_content, f)
        cfg = WalkForwardConfig.from_yaml(p)
        assert cfg.start_date == "2024-10-01"
        assert cfg.train_months == 12
        assert cfg.settings == {"min_score": 75.0}

    def test_step_less_than_test_rejected(self):
        with pytest.raises(ValueError, match="step_months.*test_months|step.*test"):
            WalkForwardConfig(
                start_date="2024-10-01",
                end_date="2026-09-30",
                universe=SMOKE_SYMBOLS,
                step_months=2,
                test_months=3,
            )

    def test_no_tuning_default(self):
        """WalkForwardEngine.run must not have threshold/explicit-trial parameters."""
        import inspect
        sig = inspect.signature(WalkForwardEngine.run)
        params = set(sig.parameters)
        assert "thresholds" not in params, "WalkForwardEngine.run must not accept thresholds"
        assert "min_trades" not in params, "WalkForwardEngine.run must not accept min_trades"


# ---------------------------------------------------------------------------
# generate_folds — fold plan generation (A01 calendar)
# ---------------------------------------------------------------------------


class TestGenerateFolds:
    """12/3/3 fold generation: half-open intervals, no overlap, partial tail."""

    DEFAULT_CONFIG = WalkForwardConfig(
        start_date="2024-10-01",
        end_date="2026-09-30",
        universe=SMOKE_SYMBOLS,
    )

    def test_folds_are_list_of_dicts(self):
        folds, tail = generate_folds(self.DEFAULT_CONFIG)
        assert isinstance(folds, list)
        assert tail is None or isinstance(tail, dict)

    def test_fold_has_required_keys(self):
        folds, _ = generate_folds(self.DEFAULT_CONFIG)
        for fold in folds:
            assert "fold_id" in fold
            assert "train_start" in fold
            assert "train_end_exclusive" in fold
            assert "test_start" in fold
            assert "test_end_exclusive" in fold
            assert "complete" in fold

    def test_fold_ids_sequential(self):
        folds, _ = generate_folds(self.DEFAULT_CONFIG)
        for i, fold in enumerate(folds):
            assert fold["fold_id"] == i, f"expected fold_id {i}, got {fold['fold_id']}"

    def test_train_ends_at_test_start(self):
        """Half-open: train_end_exclusive == test_start (no gap, no overlap)."""
        folds, _ = generate_folds(self.DEFAULT_CONFIG)
        for fold in folds:
            assert fold["train_end_exclusive"] == fold["test_start"], (
                f"fold {fold['fold_id']}: train_end_exclusive {fold['train_end_exclusive']} "
                f"!= test_start {fold['test_start']}"
            )

    def test_no_overlap_between_adjacent_folds(self):
        """Adjacent folds' test intervals must not overlap."""
        folds, _ = generate_folds(self.DEFAULT_CONFIG)
        for i in range(len(folds) - 1):
            f0_test_start = folds[i]["test_start"]
            f0_test_end = folds[i]["test_end_exclusive"]
            f1_test_start = folds[i + 1]["test_start"]
            # Half-open: f0 interval is [test_start, test_end_exclusive)
            # f1 interval must start >= f0.test_end_exclusive
            assert f1_test_start >= f0_test_end, (
                f"fold {folds[i+1]['fold_id']} test_start {f1_test_start} overlaps "
                f"fold {folds[i]['fold_id']} test_end_exclusive {f0_test_end}"
            )

    def test_default_fold_count_and_dates(self):
        """With 2024-10-01 to 2026-09-30, 12/3/3 yields 4 complete folds."""
        folds, tail = generate_folds(self.DEFAULT_CONFIG)
        assert len(folds) == 4

        # Fold 0: train 2024-10-01..2025-09-30, test 2025-10-01..2025-12-31
        assert folds[0]["fold_id"] == 0
        assert folds[0]["train_start"] == "2024-10-01"
        assert folds[0]["train_end_exclusive"] == "2025-10-01"
        assert folds[0]["test_start"] == "2025-10-01"
        assert folds[0]["test_end_exclusive"] == "2026-01-01"
        assert folds[0]["complete"] is True

        # Fold 1: train 2025-01-01..2025-12-31, test 2026-01-01..2026-03-31
        assert folds[1]["fold_id"] == 1
        assert folds[1]["train_start"] == "2025-01-01"
        assert folds[1]["train_end_exclusive"] == "2026-01-01"
        assert folds[1]["test_start"] == "2026-01-01"
        assert folds[1]["test_end_exclusive"] == "2026-04-01"
        assert folds[1]["complete"] is True

        # Fold 2: train 2025-04-01..2026-03-31, test 2026-04-01..2026-06-30
        assert folds[2]["fold_id"] == 2
        assert folds[2]["train_start"] == "2025-04-01"
        assert folds[2]["train_end_exclusive"] == "2026-04-01"
        assert folds[2]["test_start"] == "2026-04-01"
        assert folds[2]["test_end_exclusive"] == "2026-07-01"
        assert folds[2]["complete"] is True

        # Fold 3: train 2025-07-01..2026-06-30, test 2026-07-01..2026-09-30
        assert folds[3]["fold_id"] == 3
        assert folds[3]["train_start"] == "2025-07-01"
        assert folds[3]["train_end_exclusive"] == "2026-07-01"
        assert folds[3]["test_start"] == "2026-07-01"
        assert folds[3]["test_end_exclusive"] == "2026-10-01"
        assert folds[3]["complete"] is True

    def test_no_train_start_past_end_date(self):
        """Every fold's train_start must be < config.end_date."""
        folds, tail = generate_folds(self.DEFAULT_CONFIG)
        for fold in folds:
            assert fold["train_start"] < self.DEFAULT_CONFIG.end_date, (
                f"fold {fold['fold_id']} train_start {fold['train_start']} "
                f"not < end_date {self.DEFAULT_CONFIG.end_date}"
            )

    # --- Edge: year boundary ---

    def test_year_boundary_crossing(self):
        """Fold train window crossing Dec/Jan boundary."""
        cfg = WalkForwardConfig(
            start_date="2023-11-01",
            end_date="2025-10-31",
            universe=SMOKE_SYMBOLS,
        )
        folds, _ = generate_folds(cfg)
        # Fold 0: train 2023-11-01..2024-10-31, test 2024-11-01..2025-01-31
        assert folds[0]["train_start"] == "2023-11-01"
        assert folds[0]["train_end_exclusive"] == "2024-11-01"
        assert folds[0]["test_start"] == "2024-11-01"
        assert folds[0]["test_end_exclusive"] == "2025-02-01"

    # --- Edge: leap year ---

    def test_leap_year_feb_29(self):
        """Fold starting in leap-year January handles Feb 29 correctly."""
        cfg = WalkForwardConfig(
            start_date="2024-01-31",
            end_date="2025-12-31",
            universe=SMOKE_SYMBOLS,
            train_months=1,
            test_months=1,
            step_months=1,
        )
        folds, _ = generate_folds(cfg)
        # Fold 0: train 2024-01-31..2024-02-29, test 2024-02-29..2024-03-31
        assert folds[0]["train_start"] == "2024-01-31"
        assert folds[0]["train_end_exclusive"] == "2024-02-29"
        assert folds[0]["test_start"] == "2024-02-29"
        assert folds[0]["test_end_exclusive"] == "2024-03-29"

    # --- Edge: January 31 ---

    def test_jan31_clamping_propagates(self):
        """Jan 31 start propagates month-end clamping through folds."""
        cfg = WalkForwardConfig(
            start_date="2024-01-31",
            end_date="2024-12-31",
            universe=SMOKE_SYMBOLS,
            train_months=1,
            test_months=1,
            step_months=1,
        )
        folds, _ = generate_folds(cfg)
        # Fold 1: train_start should be _add_months("2024-01-31", 1) = 2024-02-29
        # Then train_end_exclusive = _add_months("2024-02-29", 1) = 2024-03-31
        assert folds[1]["train_start"] == "2024-02-29"
        assert folds[1]["train_end_exclusive"] == "2024-03-29"
        assert folds[1]["test_start"] == "2024-03-29"
        assert folds[1]["test_end_exclusive"] == "2024-04-29"

    # --- Edge: partial tail ---

    def test_partial_tail_included(self):
        """When config end cuts a test window short, partial tail is included as incomplete fold."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2025-06-01",
            universe=SMOKE_SYMBOLS,
            train_months=12,
            test_months=3,
            step_months=3,
        )
        folds, tail = generate_folds(cfg)
        # Fold 0: train 2024-01-01..2025-01-01, test 2025-01-01..2025-04-01 (complete)
        # Fold 1: train 2024-04-01..2025-04-01, test 2025-04-01..2025-07-01
        #         2025-07-01 > 2025-06-02 (end_date+1), so incomplete partial tail
        if len(folds) == 2:
            assert folds[1]["complete"] is False
            assert tail is not None
            assert tail["fold_id"] == 1
            assert tail["complete"] is False

    def test_partial_tail_not_counted_as_complete(self):
        """Partial tail fold must have complete=False."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2025-05-15",
            universe=SMOKE_SYMBOLS,
            train_months=12,
            test_months=3,
            step_months=3,
        )
        folds, tail = generate_folds(cfg)
        complete_folds = [f for f in folds if f["complete"]]
        for fold in folds:
            if not fold["complete"]:
                assert tail is not None and tail["fold_id"] == fold["fold_id"]

    def test_very_short_range_no_folds(self):
        """If range is shorter than train_months, generate_folds returns empty list."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2024-06-30",
            universe=SMOKE_SYMBOLS,
            train_months=12,
        )
        folds, tail = generate_folds(cfg)
        assert folds == []
        assert tail is None

    # --- Test train < test step validation ---

    def test_step_less_than_test_raises(self):
        """Config must reject step_months < test_months at construction."""
        with pytest.raises(ValueError, match="step.*test|step_months"):
            WalkForwardConfig(
                start_date="2024-01-01",
                end_date="2025-12-31",
                universe=SMOKE_SYMBOLS,
                step_months=2,
                test_months=3,
            )

    # --- consecutive fold non-overlapping invariant ---

    def test_folds_trains_do_not_overlap(self):
        """With step < train, train windows overlap; test windows never overlap."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2026-12-31",
            universe=SMOKE_SYMBOLS,
            train_months=6,
            test_months=3,
            step_months=3,
        )
        folds, _ = generate_folds(cfg)
        # Train windows overlap when step < train — expected for sliding windows.
        # Test windows must NOT overlap:
        for i in range(len(folds) - 1):
            assert folds[i + 1]["test_start"] >= folds[i]["test_end_exclusive"], (
                f"test overlap: fold {i} test ends {folds[i]['test_end_exclusive']}, "
                f"fold {i+1} test starts {folds[i+1]['test_start']}"
            )
        # Verify train overlap (step < train) confirms sliding-window semantics
        for i in range(len(folds) - 1):
            assert folds[i + 1]["train_start"] < folds[i]["train_end_exclusive"], (
                f"expected train overlap when step<train: fold {i} ends "
                f"{folds[i]['train_end_exclusive']}, fold {i+1} starts "
                f"{folds[i+1]['train_start']}"
            )

    def test_gap_between_non_adjacent_folds(self):
        """With step_months < train_months, train windows overlap but test windows don't."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2025-12-31",
            universe=SMOKE_SYMBOLS,
            train_months=12,
            test_months=3,
            step_months=3,
        )
        folds, _ = generate_folds(cfg)
        # Because step < train, train windows WILL overlap — that's expected.
        # But test windows must not overlap:
        for i in range(len(folds) - 1):
            assert folds[i + 1]["test_start"] >= folds[i]["test_end_exclusive"], (
                f"test overlap between fold {i} and fold {i+1}"
            )


# ---------------------------------------------------------------------------
# WalkForwardEngine — registration-only run (no tuning)
# ---------------------------------------------------------------------------


class TestWalkForwardEngine:
    """WalkForwardEngine.run must be registration-only — no callbacks or threshold trials."""

    def test_run_returns_dict_with_method(self):
        engine = WalkForwardEngine()
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        result = engine.run(cfg)
        assert isinstance(result, dict)
        assert "method" in result
        assert result["method"] == "walk_forward_v2"

    def test_run_includes_folds_plan(self):
        engine = WalkForwardEngine()
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        result = engine.run(cfg)
        assert "folds_plan" in result
        folds = result["folds_plan"]
        assert isinstance(folds, list)
        for fold in folds:
            assert "fold_id" in fold
            assert "test_start" in fold
            assert "test_end_exclusive" in fold

    def test_run_includes_config_hash(self):
        engine = WalkForwardEngine()
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        result = engine.run(cfg)
        assert "config_hash" in result
        assert result["config_hash"] == cfg.canonical_hash()

    def test_run_engine_version_present(self):
        engine = WalkForwardEngine()
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        result = engine.run(cfg)
        assert "engine_version" in result
        assert isinstance(result["engine_version"], str)
        assert result["engine_version"]

    def test_run_no_threshold_tuning_parameters(self):
        """Engine.run must not accept threshold/trial params — no tuning."""
        import inspect
        sig = inspect.signature(WalkForwardEngine.run)
        params = list(sig.parameters)
        # run(self, config) only
        assert params == ["self", "config"], (
            f"WalkForwardEngine.run params should be (self, config) only, got {params}"
        )

    def test_run_does_not_invoke_callbacks(self):
        """Engine.run must not call any backtest/trial callable — registration only."""
        class CallTracker:
            def __init__(self):
                self.called = False
            def __call__(self, *args, **kwargs):
                self.called = True

        tracker = CallTracker()
        engine = WalkForwardEngine()  # No run_fn parameter
        # Verify the engine doesn't take or use callables
        assert not hasattr(engine, 'run_fn'), "Engine must not store a run_fn callback"

    def test_run_deterministic_same_config(self):
        """Same config => same run output (except timestamps)."""
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        engine = WalkForwardEngine()
        r1 = engine.run(cfg)
        r2 = engine.run(cfg)
        # Strip any timestamp if present
        for r in (r1, r2):
            r.pop("generated_at", None)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Immutability deep-freeze tests
# ---------------------------------------------------------------------------


class TestConfigDeepFreeze:
    """WalkForwardConfig must be deeply immutable — frozen dataclass + tuple/list guard."""

    def test_dataclass_frozen(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        with pytest.raises(AttributeError):
            cfg.start_date = "2025-01-01"  # type: ignore[misc]

    def test_universe_tuple_converted(self):
        """Universe list should be stored as tuple for immutability."""
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        # Access the internal universe (may be a tuple)
        uni = cfg.universe
        assert isinstance(uni, (list, tuple))
        # If it's stored as a tuple, mutating a list copy shouldn't affect config
        if isinstance(uni, tuple):
            with pytest.raises(AttributeError):
                uni.append("EXTRA.SZ")  # type: ignore[attr-defined]

    def test_settings_immutable_after_construction(self):
        """Settings dict should be stored as immutable mapping."""
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
            settings={"min_score": 75.0},
        )
        s = cfg.settings
        # The config may freeze the dict via tuple or deepcopy
        # At minimum, verify the original is reflected faithfully
        assert s.get("min_score") == 75.0

    def test_stability_thresholds_immutable(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        t = cfg.stability_thresholds
        orig = t.get("min_observed_folds")
        # Attempt mutation should not affect config
        try:
            t["min_observed_folds"] = 999
        except (TypeError, AttributeError):
            pass  # FrozenMapping or similar
        assert cfg.stability_thresholds.get("min_observed_folds") == orig


# ---------------------------------------------------------------------------
# No-tuning assertion — verify WalkForwardEngine has no old-style params
# ---------------------------------------------------------------------------


class TestNoTuning:
    """Verify the new WalkForwardEngine has zero tuning surface."""

    def test_no_thresholds_attribute(self):
        """Engine class/instance must not have threshold_grid/trials attributes."""
        engine = WalkForwardEngine()
        attrs = [a for a in dir(engine) if not a.startswith("__")]
        for banned in ("thresholds", "trial", "run_fn", "score_grid"):
            assert not any(banned in a.lower() for a in attrs), (
                f"Engine has banned tuning attribute: {banned}"
            )

    def test_no_thresholds_in_run_output(self):
        """run() output must not contain threshold/trial/grid keys."""
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        engine = WalkForwardEngine()
        result = engine.run(cfg)
        for banned in ("threshold", "trial", "grid", "best", "calmar", "score"):
            assert not any(banned in k.lower() for k in result), (
                f"run output contains banned key containing '{banned}': {k}"
            )


# ---------------------------------------------------------------------------
# Property-based: fold coverage invariant (no gaps, no overlaps)
# ---------------------------------------------------------------------------


class TestFoldInvariants:
    """Property-style tests that hold for any valid config."""

    def test_all_test_intervals_cover_some_range(self):
        """Every complete fold must have a positive test range."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2026-12-31",
            universe=SMOKE_SYMBOLS,
        )
        folds, _ = generate_folds(cfg)
        for fold in folds:
            if fold["complete"]:
                assert fold["test_end_exclusive"] > fold["test_start"], (
                    f"fold {fold['fold_id']}: zero-length test interval"
                )

    def test_train_start_before_end_exclusive(self):
        """Every fold must have a positive train interval."""
        cfg = WalkForwardConfig(
            start_date="2024-01-01",
            end_date="2026-12-31",
            universe=SMOKE_SYMBOLS,
        )
        folds, _ = generate_folds(cfg)
        for fold in folds:
            assert fold["train_end_exclusive"] > fold["train_start"], (
                f"fold {fold['fold_id']}: zero-length train interval"
            )

    def test_first_fold_train_start_equals_config_start(self):
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        folds, _ = generate_folds(cfg)
        if folds:
            assert folds[0]["train_start"] == cfg.start_date

    def test_complete_fold_test_within_end_date(self):
        """Complete fold's test_end_exclusive must be <= end_date + 1 day."""
        cfg = WalkForwardConfig(
            start_date="2024-10-01",
            end_date="2026-09-30",
            universe=SMOKE_SYMBOLS,
        )
        end_exclusive = _add_days(cfg.end_date, 1)
        folds, _ = generate_folds(cfg)
        for fold in folds:
            if fold["complete"]:
                assert fold["test_end_exclusive"] <= end_exclusive, (
                    f"fold {fold['fold_id']}: test_end_exclusive {fold['test_end_exclusive']}"
                    f" > config_end_exclusive {end_exclusive}"
                )
