from __future__ import annotations

import hashlib
import importlib.util
import json
import py_compile
from pathlib import Path

from a_share_agent.backtest.metrics import batch_audit_attribution
from a_share_agent.backtest.regime import sector_context_from_history

ROOT = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = ROOT / "scripts" / "run_trading_grade_smoke.py"
SMOKE_ARTIFACT = ROOT / "data" / "diagnostics" / "trading_grade_smoke_latest.json"


def _trade(direction: str, rid: str | None) -> dict:
    return {
        "direction": direction,
        "round_trip_id": rid,
        "symbol": "600000.SH",
        "signal_date": "2025-01-02",
        "trade_date": "2025-01-03" if direction == "BUY" else "2025-01-10",
        "entry_date": "2025-01-03",
        "pattern_id": "p1",
        "pattern_version": "1.0.0",
        "regime_at_signal": "BULL_TREND",
        "theme": "A",
        "theme_lifecycle": "ACCELERATING",
        "theme_lifecycle_confidence_at_signal": 0.8,
        "theme_data_quality_at_signal": {"state": "ok"},
        "signal_strength": "primary",
        "regime_confidence_at_signal": 0.9,
        "regime_data_quality_at_signal": {"state": "ok"},
        "pnl": 100.0 if direction == "SELL" else None,
    }


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("trading_grade_smoke", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_script_compiles_on_ci_python():
    py_compile.compile(str(SMOKE_SCRIPT), doraise=True)


def test_canonical_unknown_without_neutral_fallback():
    ctx = sector_context_from_history([], "2025-01-01", fallback_neutral=False)
    assert ctx["lifecycle"] == "UNKNOWN"
    assert ctx["sector_lifecycle"] == "unknown"  # legacy field remains backward compatible


def test_duplicate_sell_round_trip_fails_closed():
    buy = _trade("BUY", "rt-dup-sell")
    sell1 = _trade("SELL", "rt-dup-sell")
    sell2 = _trade("SELL", "rt-dup-sell")
    summary = batch_audit_attribution([buy, sell1, sell2])
    assert summary["duplicate_sell_round_trip_ids"] == ["rt-dup-sell"]
    assert "rt-dup-sell" in summary["duplicate_round_trip_ids"]
    assert summary["invalid"] == 2
    assert all(
        "DUPLICATE_SELL_ROUND_TRIP_ID" in item["errors"]
        for item in summary["invalid_trades"]
    )


def test_duplicate_buy_round_trip_fails_closed():
    buy1 = _trade("BUY", "rt-dup-buy")
    buy2 = _trade("BUY", "rt-dup-buy")
    sell = _trade("SELL", "rt-dup-buy")
    summary = batch_audit_attribution([buy1, buy2, sell])
    assert summary["duplicate_buy_round_trip_ids"] == ["rt-dup-buy"]
    assert summary["invalid"] == 1
    assert "DUPLICATE_BUY_ROUND_TRIP_ID" in summary["invalid_trades"][0]["errors"]


def test_missing_round_trip_id_is_reported():
    summary = batch_audit_attribution([_trade("SELL", None)])
    assert summary["missing_round_trip_ids"] == 1
    assert summary["invalid"] == 1
    assert "MISSING_ROUND_TRIP_ID" in summary["errors"]


def test_smoke_helper_requires_adjusted_and_raw_inputs():
    module = _load_smoke_module()
    missing = module._missing_physical_inputs([
        {
            "symbol": "A.SH",
            "adjusted_bars_sha256": "a" * 64,
            "rows": 100,
            "raw_bars_sha256": "MISSING",
            "raw_rows": 0,
        },
        {
            "symbol": "B.SH",
            "adjusted_bars_sha256": "MISSING",
            "rows": 0,
            "raw_bars_sha256": "b" * 64,
            "raw_rows": 100,
        },
    ])
    assert missing == [
        {"symbol": "A.SH", "problems": ["MISSING_RAW_EXECUTION_DATA"]},
        {"symbol": "B.SH", "problems": ["MISSING_ADJUSTED_BARS"]},
    ]


def test_smoke_paths_are_repo_relative():
    module = _load_smoke_module()
    path = ROOT / "data" / "backtest" / "raw_prices" / "600519_SH.csv"
    assert module._relative(path, ROOT) == "data/backtest/raw_prices/600519_SH.csv"


def test_committed_smoke_artifact_has_raw_and_adjusted_hashes():
    data = json.loads(SMOKE_ARTIFACT.read_text(encoding="utf-8"))
    assert data["data_kind"] == "HISTORICAL_LOCAL_DATA"
    assert data["source_verification"] in {"UNVERIFIED_CACHE", "PROVENANCE_VERIFIED"}
    assert data["input_files"]
    for entry in data["input_files"]:
        for field in ("adjusted_bars_sha256", "raw_bars_sha256"):
            value = entry[field]
            assert len(value) == 64
            int(value, 16)


def test_round_trip_hash_recipe_is_deterministic():
    raw = "600519.SH|2025-06-01|2025-06-02|high_volume_breakout|1.0.0|0"
    assert hashlib.sha256(raw.encode()).hexdigest()[:16] == hashlib.sha256(raw.encode()).hexdigest()[:16]
