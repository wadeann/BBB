from __future__ import annotations

from pathlib import Path

import pytest

from a_share_agent.strategy.pattern_registry import PatternRegistry, PatternSpec
from a_share_agent.strategy.signal_engine import DeterministicSignalEngine


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def registry() -> PatternRegistry:
    return DeterministicSignalEngine.build_registry()


@pytest.fixture
def engine() -> DeterministicSignalEngine:
    return DeterministicSignalEngine()


@pytest.fixture
def sample_bars() -> list[dict]:
    """Generate 65 bars suitable for pattern detection.

    Bars 0-9:   uptrend
    Bars 10-34: decline (41% drop, volume fading to near-zero — triggers rebound candidate)
    Bars 35-44: consolidation (tight range, low volume)
    Bars 45-54: rebound up (rising close, above MA5)
    Bars 55-64: breakout continuation (high volume, new high)
    """
    bars: list[dict] = []
    for i in range(65):
        if i < 10:
            p = 10.0 + i * 0.15            # 10.0 -> 11.35
        elif i < 35:
            p = 11.35 - (i - 9) * 0.18     # 11.35 -> 6.67
        elif i < 45:
            p = 6.67 + (i - 34) * 0.03     # 6.67 -> 6.97
        else:
            p = 6.97 + (i - 44) * 0.25     # 6.97 -> 11.97

        # Volume: crashes hard during decline (selling exhaustion), tiny at consolidation
        if i < 10:
            vol = 800_000
        elif i < 20:
            vol = max(400_000, 800_000 - (i - 9) * 80_000)
        elif i < 35:
            vol = max(15_000, 400_000 - (i - 19) * 30_000)
        elif i < 45:
            vol = 50_000
        else:
            vol = 600_000 + (i - 44) * 100_000

        prev_close = bars[-1]["close"] if bars else p
        pct = round((p - prev_close) / prev_close * 100, 2) if bars else 0.0

        # Realistic OHLC: uptrend/rebound bars are green, decline bars are red
        # Near the end of the decline, candle bodies shrink (selling exhaustion)
        if i < 10 or i >= 35:
            o = p - 0.03
            h = p + 0.06
            lo = p - 0.04
        else:
            o = prev_close
            # Body shrinks as decline matures (last 10 decline bars have tiny bodies)
            gap = o - p  # typical gap between open (prev_close) and close (today's p)
            if i >= 25:
                # Exhaustion: small bodies
                adj_o = prev_close
                adj_c = adj_o - gap * 0.15
            else:
                adj_o = o
                adj_c = p
            h = max(adj_o + 0.02, adj_c + 0.02)
            lo = min(adj_c - 0.02, adj_o - 0.03)

        bars.append({
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(lo, 2),
            "close": round(p, 2),
            "volume": int(vol),
            "pct": pct,
        })
    return bars


# ------------------------------------------------------------------
# D01: All 18 patterns registered (14 daily + 4 intraday)
# ------------------------------------------------------------------

PATTERN_IDS = frozenset({
    "triple_golden_cross", "ma_convergence_breakout", "high_volume_breakout",
    "ma60_breakout_retest", "single_bull_hold", "ma5_momentum_pullback",
    "low_volume_support_bull",
    "rebound_candidate", "rebound_confirmation",
    "long_bull_day7",
    "shooting_star_high", "volume_price_divergence", "ma20_break", "ma_bearish_cut",
    "morning_surge", "vwap_hold", "afternoon_breakout", "vwap_break_warning",
})

FAMILIES = frozenset({
    "trend_breakout", "trend_pullback", "rebound_reversal",
    "pattern_confirmation", "exit_defensive", "intraday_momentum",
})


def test_all_18_patterns_registered(registry: PatternRegistry) -> None:
    all_specs = registry.list()
    ids = {s.pattern_id for s in all_specs}
    assert ids == PATTERN_IDS, f"Missing: {PATTERN_IDS - ids} | Extra: {ids - PATTERN_IDS}"
    assert len(all_specs) == 18


def test_all_patterns_have_version_1_0_0(registry: PatternRegistry) -> None:
    for spec in registry.list():
        assert spec.pattern_version == "1.0.0", f"{spec.pattern_id} version != 1.0.0"


def test_all_patterns_have_family(registry: PatternRegistry) -> None:
    for spec in registry.list():
        assert spec.family in FAMILIES, f"{spec.pattern_id} unknown family {spec.family}"


def test_family_counts(registry: PatternRegistry) -> None:
    counts = {"trend_breakout": 3, "trend_pullback": 4, "rebound_reversal": 2,
              "pattern_confirmation": 1, "exit_defensive": 5, "intraday_momentum": 3}
    for family, expected in counts.items():
        assert len(registry.list(family=family)) == expected, \
            f"{family} count mismatch: expected {expected}"


def test_required_features_and_detect_func(registry: PatternRegistry) -> None:
    rebound = registry.list(family="rebound_reversal")
    for spec in rebound:
        assert spec.detect_func_name, f"{spec.pattern_id} missing detect_func_name"


# ------------------------------------------------------------------
# D02: get / list / find work correctly
# ------------------------------------------------------------------

def test_get_returns_spec(registry: PatternRegistry) -> None:
    spec = registry.get("triple_golden_cross")
    assert spec is not None
    assert spec.pattern_id == "triple_golden_cross"
    assert spec.family == "trend_breakout"


def test_get_unknown_returns_none(registry: PatternRegistry) -> None:
    assert registry.get("nonexistent_pattern") is None


def test_get_version_mismatch_returns_none(registry: PatternRegistry) -> None:
    assert registry.get("triple_golden_cross", version="9.9.9") is None


def test_get_exact_version(registry: PatternRegistry) -> None:
    spec = registry.get("triple_golden_cross", version="1.0.0")
    assert spec is not None
    assert spec.pattern_version == "1.0.0"


def test_list_all(registry: PatternRegistry) -> None:
    all_specs = registry.list()
    assert len(all_specs) == 18


def test_list_by_family(registry: PatternRegistry) -> None:
    breakout = registry.list(family="trend_breakout")
    assert all(s.family == "trend_breakout" for s in breakout)
    assert len(breakout) == 3


def test_list_empty_family(registry: PatternRegistry) -> None:
    assert registry.list(family="nonexistent_family") == []


def test_find_with_engine(registry: PatternRegistry, engine: DeterministicSignalEngine,
                          sample_bars: list[dict]) -> None:
    """find() should run detection functions and return hits."""
    # Slice to the end of the decline — rebound_candidate should trigger
    decline_slice = sample_bars[:35]
    hits_mid = registry.find(engine, decline_slice)
    rebound_hits = [h for h in hits_mid if h.get("pattern_id") == "rebound_candidate"]
    assert len(rebound_hits) >= 1, (
        f"Expected rebound_candidate hit at bar 35, got: {hits_mid}"
    )


# ------------------------------------------------------------------
# D03: scan() unchanged with or without registry
# ------------------------------------------------------------------

def test_scan_unchanged_with_registry(engine: DeterministicSignalEngine,
                                      sample_bars: list[dict]) -> None:
    """Building the registry does not alter scan() output."""
    result_before = engine.scan(sample_bars, market_regime="bull", sector_strength="strong")
    _ = DeterministicSignalEngine.build_registry()
    result_after = engine.scan(sample_bars, market_regime="bull", sector_strength="strong")
    assert result_before == result_after, "scan() output changed after build_registry()"


def test_scan_unchanged_without_registry_still_works(engine: DeterministicSignalEngine,
                                                      sample_bars: list[dict]) -> None:
    """scan() works as before even if build_registry() is never called."""
    result = engine.scan(sample_bars)
    assert isinstance(result, list)


# ------------------------------------------------------------------
# D04: Pattern metadata queryable
# ------------------------------------------------------------------

def test_pattern_metadata_queryable(registry: PatternRegistry) -> None:
    spec = registry.get("ma60_breakout_retest")
    assert spec is not None
    assert spec.required_features == ["ma60", "vv20"]
    assert spec.family == "trend_pullback"
    assert spec.entry_rule is not None
    assert spec.invalidation_rule is not None
    assert spec.exit_rule is not None


def test_yaml_pattern_metadata_valid() -> None:
    """Verify YAML mirrors the registry."""
    conf_path = Path(__file__).parents[1] / "config" / "pattern_enablement.yaml"
    assert conf_path.exists(), "pattern_enablement.yaml not found"
    import yaml
    with open(conf_path, "r") as f:
        data = yaml.safe_load(f)
    patterns = data.get("patterns", [])
    yaml_ids = {p["pattern_id"] for p in patterns}
    assert yaml_ids == PATTERN_IDS, f"YAML missing patterns: {PATTERN_IDS - yaml_ids}"


# ------------------------------------------------------------------
# D05: Versioning
# ------------------------------------------------------------------

def test_versioning_same_id_different_versions(registry: PatternRegistry) -> None:
    """Register same ID with a different version — both should exist."""
    spec_v2 = PatternSpec(
        pattern_id="triple_golden_cross",
        pattern_version="2.0.0",
        family="trend_breakout",
        required_features=["ma5", "ma10", "ma20", "vv5", "vv10", "macd"],
    )
    registry.register(spec_v2)
    assert registry.get("triple_golden_cross", version="1.0.0") is not None
    assert registry.get("triple_golden_cross", version="2.0.0") is not None
    default = registry.get("triple_golden_cross")
    assert default is not None
    assert default.pattern_version == "2.0.0"


def test_duplicate_registration_raises(registry: PatternRegistry) -> None:
    spec = PatternSpec(
        pattern_id="dup_test",
        pattern_version="1.0.0",
        family="trend_breakout",
    )
    registry.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

def test_find_empty_bars(registry: PatternRegistry, engine: DeterministicSignalEngine) -> None:
    hits = registry.find(engine, [])
    assert hits == []


def test_registry_persistence_across_instances() -> None:
    """Two calls to build_registry() produce independent registries."""
    r1 = DeterministicSignalEngine.build_registry()
    r2 = DeterministicSignalEngine.build_registry()
    r1.register(PatternSpec(pattern_id="custom_a", pattern_version="1.0.0", family="trend_breakout"))
    r2.register(PatternSpec(pattern_id="custom_b", pattern_version="1.0.0", family="trend_pullback"))
    assert r1.get("custom_a") is not None
    assert r2.get("custom_b") is not None
    assert r1.get("custom_b") is None
    assert r2.get("custom_a") is None


def test_signal_hit_to_dict_has_pattern_id(sample_bars: list[dict]) -> None:
    """SignalHit.to_dict() should include pattern_id and pattern_version."""
    engine = DeterministicSignalEngine()
    results = engine.scan(sample_bars, market_regime="bull", sector_strength="strong")
    for r in results:
        assert "pattern_id" in r, f"Missing pattern_id in {r.get('signal')}"
        assert "pattern_version" in r, f"Missing pattern_version in {r.get('signal')}"
        assert r["pattern_version"] == "1.0.0"


def test_rebound_candidate_detection(engine: DeterministicSignalEngine) -> None:
    """Verify _rebound_candidate triggers on a 35%+ drop with exhaustion candles."""
    bars = []
    for i in range(30):
        if i < 5:
            p = 10.0 + i * 0.2
        else:
            p = 11.0 - (i - 4) * 0.35  # crash ~45%
        vol = max(12_000, 500_000 - i * 18_000)
        prev_close = bars[-1]["close"] if bars else p
        pct = round((p - prev_close) / prev_close * 100, 2) if bars else 0.0
        # Decline bars: large bodies early, tiny bodies late (exhaustion)
        if i < 5:
            o = p - 0.03
            h = p + 0.06
            lo = p - 0.04
        else:
            o = prev_close
            gap = o - p
            # Last 10 bars: small exhaustion candles
            if i >= 20:
                body = gap * 0.12
                mid = (o + p) / 2
                o = mid + body / 2
                p = mid - body / 2
            h = max(o + 0.01, p + 0.01)
            lo = min(p - 0.01, o - 0.02)
        bars.append({
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(lo, 2),
            "close": round(p, 2),
            "volume": int(vol),
            "pct": pct,
        })
    result = engine._rebound_candidate(bars)
    assert result is not None, f"_rebound_candidate returned None for 30-bar crash data"
    assert result["pattern_id"] == "rebound_candidate"
    assert result["signal"] == "rebound_candidate"
    assert result["evidence"]["drop_pct"] >= 0.15
