"""Walk-forward fold planning and registration — Phase 2A.1 frozen contract.

Replaces the legacy threshold-tuning WalkForwardEngine with a fixed-rule,
no-tuning configuration and fold generator. Half-open monthly intervals
(12/3/3 default), external end-date inclusive, anchored month arithmetic.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

# ---------------------------------------------------------------------------
# Month arithmetic — half-open anchored
# ---------------------------------------------------------------------------

_STABILITY_THRESHOLDS: dict[str, Any] = {
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


def _add_months(s: str, months: int) -> str:
    """Add *months* to ISO date *s*, clamping to month-end when needed.

    Examples:
        _add_months("2024-01-31", 1) -> "2024-02-29" (leap year)
        _add_months("2024-01-15", 12) -> "2025-01-15"
    """
    import calendar

    d = date.fromisoformat(s)
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day).isoformat()


def _add_days(s: str, days: int) -> str:
    """Add *days* to ISO date *s*."""
    return (date.fromisoformat(s) + timedelta(days=days)).isoformat()


def _deep_freeze(value: Any) -> Any:
    """Recursively freeze mutable objects for immutable config fields."""
    if isinstance(value, dict):
        return {k: _deep_freeze(v) for k, v in value.items()}
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# WalkForwardConfig — frozen configuration
# ---------------------------------------------------------------------------


class WalkForwardConfig:
    """Immutable walk-forward configuration with canonical hashing.

    Parameters
    ----------
    start_date : str
        First date of the overall evaluation period (ISO, inclusive).
    end_date : str
        Last date of the overall evaluation period (ISO, inclusive).
    universe : list[str]
        Symbols to include in the walk-forward study.
    settings : dict, optional
        BacktestSettings overrides (e.g. min_score, max_positions).
    train_months : int, default 12
        Training window length in months.
    test_months : int, default 3
        Test (OOS) window length in months.
    step_months : int, default 3
        Step size between successive fold start dates, in months.
    warmup_bars : int, default 260
        Minimum number of bars required for warmup.
    stability_thresholds : dict, optional
        OOS stability evaluation thresholds. Falls back to module defaults.
    """

    __slots__ = (
        "_start_date",
        "_end_date",
        "_universe",
        "_settings",
        "_train_months",
        "_test_months",
        "_step_months",
        "_warmup_bars",
        "_stability_thresholds",
        "_hash",
    )

    def __init__(
        self,
        start_date: str,
        end_date: str,
        universe: list[str],
        settings: dict[str, Any] | None = None,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 3,
        warmup_bars: int = 260,
        stability_thresholds: dict[str, Any] | None = None,
    ) -> None:
        if step_months < test_months:
            raise ValueError(
                f"step_months ({step_months}) must be >= test_months ({test_months})"
            )

        object.__setattr__(self, "_start_date", start_date)
        object.__setattr__(self, "_end_date", end_date)
        object.__setattr__(self, "_universe", tuple(universe))
        object.__setattr__(
            self, "_settings", MappingProxyType(dict(settings)) if settings else MappingProxyType({})
        )
        object.__setattr__(self, "_train_months", train_months)
        object.__setattr__(self, "_test_months", test_months)
        object.__setattr__(self, "_step_months", step_months)
        object.__setattr__(self, "_warmup_bars", warmup_bars)
        object.__setattr__(
            self,
            "_stability_thresholds",
            MappingProxyType(dict(stability_thresholds)) if stability_thresholds else MappingProxyType(dict(_STABILITY_THRESHOLDS)),
        )
        object.__setattr__(self, "_hash", None)

    # -- read-only properties -------------------------------------------------

    @property
    def start_date(self) -> str:
        return self._start_date

    @property
    def end_date(self) -> str:
        return self._end_date

    @property
    def universe(self) -> tuple[str, ...]:
        return self._universe

    @property
    def settings(self) -> dict[str, Any]:
        return self._settings

    @property
    def train_months(self) -> int:
        return self._train_months

    @property
    def test_months(self) -> int:
        return self._test_months

    @property
    def step_months(self) -> int:
        return self._step_months

    @property
    def warmup_bars(self) -> int:
        return self._warmup_bars

    @property
    def stability_thresholds(self) -> dict[str, Any]:
        return self._stability_thresholds

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation (JSON-compatible)."""
        return {
            "start_date": self._start_date,
            "end_date": self._end_date,
            "universe": list(self._universe),
            "settings": dict(self._settings),
            "train_months": self._train_months,
            "test_months": self._test_months,
            "step_months": self._step_months,
            "warmup_bars": self._warmup_bars,
            "stability_thresholds": dict(self._stability_thresholds),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WalkForwardConfig:
        """Create a WalkForwardConfig from a dict (inverse of to_dict)."""
        return cls(
            start_date=d["start_date"],
            end_date=d["end_date"],
            universe=list(d["universe"]),
            settings=d.get("settings", {}),
            train_months=d.get("train_months", 12),
            test_months=d.get("test_months", 3),
            step_months=d.get("step_months", 3),
            warmup_bars=d.get("warmup_bars", 260),
            stability_thresholds=d.get("stability_thresholds"),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> WalkForwardConfig:
        """Load config from a YAML file."""
        import yaml

        with open(path, "r") as f:
            d = yaml.safe_load(f)
        return cls.from_dict(d)

    # -- canonical hash -------------------------------------------------------

    def canonical_hash(self) -> str:
        """SHA-256 hex digest of a canonical JSON representation.

        Deterministic across runs, Python versions, and platforms.
        """
        if self._hash is not None:
            return self._hash

        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        object.__setattr__(self, "_hash", h)
        return h

    # -- equality / repr ------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WalkForwardConfig):
            return NotImplemented
        return self.canonical_hash() == other.canonical_hash()

    def __hash__(self) -> int:
        return hash(self.canonical_hash())

    def __repr__(self) -> str:
        return (
            f"WalkForwardConfig(start={self._start_date}, end={self._end_date}, "
            f"train={self._train_months}, test={self._test_months}, "
            f"step={self._step_months}, universe={len(self._universe)} symbols)"
        )


# ---------------------------------------------------------------------------
# generate_folds — fold plan generation
# ---------------------------------------------------------------------------


def generate_folds(
    config: WalkForwardConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Generate fold plan from *config*.

    Returns
    -------
    folds : list[dict]
        Each dict has keys: ``fold_id``, ``train_start``, ``train_end_exclusive``,
        ``test_start``, ``test_end_exclusive``, ``complete``.
        Only folds whose test window starts within the data range are included.
        A fold is **complete** when its test window fits fully within
        ``[start_date, end_date]`` (half-open).
    tail : dict | None
        The last incomplete fold if any, or ``None`` when every generated fold
        is complete.
    """
    folds: list[dict[str, Any]] = []
    tail: dict[str, Any] | None = None

    end_exclusive = _add_days(config.end_date, 1)  # external end -> half-open
    i = 0

    while True:
        train_start = _add_months(config.start_date, i * config.step_months)

        # Stop if training would start at or past the end date
        if train_start >= config.end_date:
            break

        train_end_exclusive = _add_months(train_start, config.train_months)
        test_start = train_end_exclusive
        test_end_exclusive = _add_months(test_start, config.test_months)

        # Stop if the test window has no data at all
        if test_start >= end_exclusive:
            break

        complete = test_end_exclusive <= end_exclusive

        fold = {
            "fold_id": i,
            "train_start": train_start,
            "train_end_exclusive": train_end_exclusive,
            "test_start": test_start,
            "test_end_exclusive": test_end_exclusive,
            "complete": complete,
        }
        folds.append(fold)

        if not complete and tail is None:
            tail = fold

        i += 1

    return folds, tail


# ---------------------------------------------------------------------------
# WalkForwardEngine — registration-only runner
# ---------------------------------------------------------------------------


class WalkForwardEngine:
    """Fixed-rule walk-forward engine — no threshold tuning, no callbacks.

    ``.run(config)`` generates the fold plan and returns metadata. It does
    **not** execute backtests or invoke any callable — that is the
    responsibility of the caller/runner layer.
    """

    def run(self, config: WalkForwardConfig) -> dict[str, Any]:
        """Register the config, compute the fold plan, and return metadata.

        Parameters
        ----------
        config : WalkForwardConfig
            Frozen walk-forward configuration.

        Returns
        -------
        dict
            Keys: ``method``, ``folds_plan``, ``config_hash``, ``engine_version``.
        """
        folds, omitted_tail = generate_folds(config)

        return {
            "method": "walk_forward_v2",
            "folds_plan": folds,
            "omitted_partial_tail": omitted_tail,
            "config_hash": config.canonical_hash(),
            "engine_version": "2a.1",
        }
