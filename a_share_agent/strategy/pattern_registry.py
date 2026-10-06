from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class PatternSpec:
    """Immutable specification for a single pattern registered in the system.

    Fields
    ------
    pattern_id : str
        Canonical pattern name, e.g. ``"triple_golden_cross"``.
    pattern_version : str
        SemVer string; any detection-logic change bumps this.  All existing
        patterns start at ``"1.0.0"``.
    family : str
        High-level category — one of ``trend_breakout``, ``trend_pullback``,
        ``rebound_reversal``, ``pattern_confirmation``, ``exit_defensive``.
    required_features : list[str]
        Feature/indicator names that must be computable before the detection
        function can run (e.g. ``["ma5", "ma10", "ma20"]``).
    detect_func_name : str
        Name of the detection method on ``DeterministicSignalEngine``.
    entry_rule : dict
        Rule stub for entry conditions (may be populated later by policy).
    invalidation_rule : dict
        Rule stub for invalidation conditions.
    exit_rule : dict
        Rule stub for exit/stop conditions.
    """
    pattern_id: str
    pattern_version: str
    family: str
    required_features: list[str] = field(default_factory=list)
    detect_func_name: str = ""
    entry_rule: dict = field(default_factory=dict)
    invalidation_rule: dict = field(default_factory=dict)
    exit_rule: dict = field(default_factory=dict)


class PatternRegistry:
    """Versioned catalog of pattern specifications.

    Supports concurrent versions of the same pattern_id and exposes
    lookup, filtering, and discovery methods.
    """

    def __init__(self) -> None:
        self._specs: list[PatternSpec] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: PatternSpec) -> None:
        """Add *spec* to the registry.

        Raises ``ValueError`` if an identical (pattern_id, pattern_version)
        pair is already registered.
        """
        for existing in self._specs:
            if existing.pattern_id == spec.pattern_id and existing.pattern_version == spec.pattern_version:
                raise ValueError(
                    f"Pattern {spec.pattern_id} v{spec.pattern_version} already registered"
                )
        self._specs.append(spec)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, pattern_id: str, version: str | None = None) -> PatternSpec | None:
        """Look up a pattern by id.

        Parameters
        ----------
        pattern_id : str
            Canonical pattern name.
        version : str, optional
            Exact semver string.  When *None* (default) the **latest**
            version (by semver tuple comparison) is returned.

        Returns
        -------
        PatternSpec | None
        """
        candidates = [s for s in self._specs if s.pattern_id == pattern_id]
        if not candidates:
            return None
        if version is not None:
            for s in candidates:
                if s.pattern_version == version:
                    return s
            return None
        # Return the newest version
        candidates.sort(key=_semver_key, reverse=True)
        return candidates[0]

    def list(self, family: str | None = None) -> list[PatternSpec]:
        """Return all registered specs, optionally filtered by *family*."""
        if family is None:
            return list(self._specs)
        return [s for s in self._specs if s.family == family]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def find(self, engine: Any, bars: list[dict[str, Any]], **context: Any) -> list[dict[str, Any]]:
        """Run all registered detection functions against *bars*.

        Each detection function is looked up on *engine* (a
        ``DeterministicSignalEngine`` instance) by
        ``spec.detect_func_name`` and called with ``bars`` as its sole
        positional argument.

        Parameters
        ----------
        engine : DeterministicSignalEngine
            Engine instance that carries the detection methods.
        bars : list[dict]
            OHLCV bar data.
        **context
            Extra keyword arguments forwarded to each detection function
            (e.g. ``market_regime=..., sector_strength=...``).

        Returns
        -------
        list[dict]
            List of signal dicts (``SignalHit.to_dict()`` format).  Only
            hits where the detection function returned a truthy value
            (dict with a truthy key, or a non-empty result) are included.
        """
        results: list[dict[str, Any]] = []
        for spec in self._specs:
            func: Callable[..., Any] | None = getattr(engine, spec.detect_func_name, None)
            if func is None:
                continue
            try:
                result = func(bars, **context)
            except Exception:
                continue
            if result and _is_positive_hit(result):
                results.append(result)
        return results


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _semver_key(spec: PatternSpec) -> tuple[int, int, int]:
    parts = spec.pattern_version.split(".")
    maj = int(parts[0]) if len(parts) > 0 else 0
    min_ = int(parts[1]) if len(parts) > 1 else 0
    pat = int(parts[2]) if len(parts) > 2 else 0
    return (maj, min_, pat)


def _is_positive_hit(result: Any) -> bool:
    """Heuristic: a detection result is positive if it's a dict with at
    least one truthy evidence value, or a list of such dicts."""
    if isinstance(result, dict):
        return bool(result.get("signal")) or bool(result.get("pattern_id"))
    if isinstance(result, list):
        return any(_is_positive_hit(r) for r in result)
    return bool(result)
