#!/usr/bin/env python3
"""Trading Grade Smoke — canonical HISTORICAL_LOCAL_DATA smoke backtest.

Usage:
    python scripts/run_trading_grade_smoke.py [--output PATH]

Requires cached adjusted historical data in data/backtest/cache/bars/ and raw
execution data in data/backtest/raw_prices/. The smoke is cache-only (no MCP),
records physical input hashes, and fails closed if required inputs are missing.

Outputs a machine-readable JSON artifact to
    data/diagnostics/trading_grade_smoke_latest.json
or the path specified by --output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.engine import BacktestEngine
from a_share_agent.backtest.metrics import (
    batch_audit_attribution,
    entry_context_quality,
    regime_pattern_matrix,
)
from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.config import load_config

SMOKE_SYMBOLS = [
    "600519.SH",
    "600036.SH",
    "600900.SH",
    "600276.SH",
    "600887.SH",
    "000333.SZ",
    "000858.SZ",
    "002415.SZ",
    "601318.SH",
    "000725.SZ",
]

SMOKE_START = "2025-06-01"
SMOKE_END = "2025-09-30"

SMOKE_PARAMS = dict(
    initial_cash=10_000_000.0,
    benchmark="000300.SH",
    min_score=75.0,
    max_holding_days=20,
    max_positions=5,
    commission_rate=0.0003,
    commission_min=5.0,
    stamp_tax_rate_sell=0.0005,
    transfer_fee_rate=0.00001,
    slippage_bps=5.0,
)


def _git_sha() -> str:
    try:
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _check_data_kind(provider: HistoricalDataProvider) -> str:
    """Classify the local historical input without overclaiming provenance."""
    bars = provider.bars(SMOKE_SYMBOLS[0])
    if not bars:
        return "NO_BARS"
    first = bars[0]["date"]
    last = bars[-1]["date"]
    if first < "2022-01-01":
        return "SUSPICIOUS_EARLY"
    if last < "2025-01-01":
        return "STALE_CACHE"
    return "HISTORICAL_LOCAL_DATA"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _per_symbol_inputs(provider: HistoricalDataProvider, symbols: list[str], root: Path) -> list[dict[str, Any]]:
    """Record physical adjusted/raw inputs and hashes for every tested symbol."""
    inputs: list[dict[str, Any]] = []
    for sym in symbols:
        fsafe = sym.replace(".", "_")
        cache_path = root / "data" / "backtest" / "cache" / "bars" / f"{fsafe}.json"
        raw_path = root / "data" / "backtest" / "raw_prices" / f"{fsafe}.csv"
        bars = provider.bars(sym)
        raw_bars = provider.raw_bars(sym)
        inputs.append({
            "symbol": sym,
            "adjusted_bars_source_path": _relative(cache_path, root),
            "adjusted_bars_sha256": _file_sha256(cache_path) if cache_path.exists() else "MISSING",
            "raw_bars_source_path": _relative(raw_path, root),
            "raw_bars_sha256": _file_sha256(raw_path) if raw_path.exists() else "MISSING",
            "rows": len(bars) if bars else 0,
            "first_date": bars[0]["date"] if bars else None,
            "last_date": bars[-1]["date"] if bars else None,
            "raw_rows": len(raw_bars) if raw_bars else 0,
            "raw_first_date": raw_bars[0]["date"] if raw_bars else None,
            "raw_last_date": raw_bars[-1]["date"] if raw_bars else None,
        })
    return inputs


def _missing_physical_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing = []
    for entry in inputs:
        problems = []
        if entry.get("adjusted_bars_sha256") == "MISSING" or int(entry.get("rows") or 0) <= 0:
            problems.append("MISSING_ADJUSTED_BARS")
        if entry.get("raw_bars_sha256") == "MISSING" or int(entry.get("raw_rows") or 0) <= 0:
            problems.append("MISSING_RAW_EXECUTION_DATA")
        if problems:
            missing.append({"symbol": entry.get("symbol"), "problems": problems})
    return missing


def run_smoke(output_path: str | Path) -> dict[str, Any]:
    src_root = Path(__file__).resolve().parent.parent
    cfg = load_config(src_root)
    provider = HistoricalDataProvider(src_root, mcp=None, use_cache=True)

    data_kind = _check_data_kind(provider)
    source_verification = "UNVERIFIED_CACHE"
    if data_kind != "HISTORICAL_LOCAL_DATA":
        return {
            "error": f"Data check failed: {data_kind}",
            "data_kind": data_kind,
            "source_verification": source_verification,
        }

    settings = BacktestSettings(
        start_date=SMOKE_START,
        end_date=SMOKE_END,
        **SMOKE_PARAMS,
    )

    tested_symbols = [sym for sym in SMOKE_SYMBOLS if provider.bars(sym)]
    skipped_symbols = [sym for sym in SMOKE_SYMBOLS if sym not in tested_symbols]
    if not tested_symbols:
        return {
            "error": "NO_DATA",
            "data_kind": data_kind,
            "reason": "All requested symbols have no cached adjusted data",
            "source_verification": source_verification,
        }

    input_files = _per_symbol_inputs(provider, tested_symbols, src_root)
    missing_inputs = _missing_physical_inputs(input_files)
    if missing_inputs:
        return {
            "error": "MISSING_PHYSICAL_INPUTS",
            "data_kind": data_kind,
            "source_verification": source_verification,
            "missing_inputs": missing_inputs,
            "input_files": input_files,
        }

    engine = BacktestEngine(cfg, provider, settings)
    report = engine.run(tested_symbols)
    trades = report.get("trades", [])
    audit = batch_audit_attribution(trades)
    matrix = regime_pattern_matrix(trades)

    sell_trades = [t for t in trades if t.get("direction") == "SELL"]
    buy_trades = [t for t in trades if t.get("direction") == "BUY"]
    context_usable = sum(1 for t in sell_trades if entry_context_quality(t)["usable"])

    artifact: dict[str, Any] = {
        "artifact_version": "1.3.0",
        "git_commit_sha": _git_sha(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_kind": data_kind,
        "source_verification": source_verification,
        "provider": "HistoricalDataProvider",
        "data_source": "data/backtest/cache/bars/",
        "date_range": {"start": SMOKE_START, "end": SMOKE_END},
        "symbols_requested": list(SMOKE_SYMBOLS),
        "symbols_tested": tested_symbols,
        "symbols_skipped": skipped_symbols,
        "universe_mode": "prefer_point_in_time",
        "input_files": input_files,
        "total_trades": len(trades),
        "buy_trades": len(buy_trades),
        "closed_trades": len(sell_trades),
        "context_usable_trades": context_usable,
        "context_unusable_trades": len(sell_trades) - context_usable,
        "patterns_seen": sorted(set(t.get("pattern_id") for t in trades if t.get("pattern_id"))),
        "regimes_seen": sorted(set(t.get("regime_at_signal") for t in trades if t.get("regime_at_signal"))),
        "lifecycles_seen": sorted(set(t.get("theme_lifecycle") for t in trades if t.get("theme_lifecycle"))),
        "unknown_lifecycle_count": sum(1 for t in trades if t.get("theme_lifecycle") == "UNKNOWN"),
        "degraded_context_trade_count": sum(1 for t in sell_trades if not entry_context_quality(t)["usable"]),
        "attribution_audit": audit,
        "regime_pattern_matrix": matrix,
        "metrics": report.get("metrics", {}),
        "sample_trades": sorted(sell_trades, key=lambda t: str(t.get("trade_date", "") or ""))[:5],
        "coverage": report.get("coverage", {}),
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, default=str, ensure_ascii=False)
    print(f"Smoke artifact written to {out}")

    print("\n=== TRADING GRADE SMOKE ===")
    print(f"  data_kind:           {data_kind}")
    print(f"  symbols_tested:      {len(tested_symbols)}")
    print(f"  date_range:          {SMOKE_START} ~ {SMOKE_END}")
    print(f"  total_trades:        {len(trades)}")
    print(f"  closed_trades:       {len(sell_trades)}")
    print(f"  patterns_seen:       {artifact['patterns_seen']}")
    print(f"  regimes_seen:        {artifact['regimes_seen']}")
    print(f"  attrib_valid:        {audit['valid']}")
    print(f"  attrib_invalid:      {audit['invalid']}")
    if audit.get("invalid_trades"):
        errors = [e for item in audit["invalid_trades"] for e in item.get("errors", [])]
        print(f"  attrib_errors:       {errors}")
    source_verification_value = artifact["source_verification"]
    print(f"  source_verification: {source_verification_value}")
    print(f"  git_sha:             {artifact['git_commit_sha']}")
    print(f"  artifact:            {out}")

    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Grade Smoke Backtest")
    default_out = Path(__file__).resolve().parent.parent / "data" / "diagnostics" / "trading_grade_smoke_latest.json"
    parser.add_argument("--output", default=str(default_out), help="Output artifact path")
    args = parser.parse_args()

    artifact = run_smoke(args.output)
    if "error" in artifact:
        print(f"\nERROR: {artifact['error']}", file=sys.stderr)
        print(f"  source_verification: {artifact.get('source_verification', '?')}", file=sys.stderr)
        return 1

    audit = artifact.get("attribution_audit", {})
    if audit.get("invalid", 0) > 0 or audit.get("duplicate_round_trip_ids"):
        print("\nERROR: attribution audit failed", file=sys.stderr)
        print(f"  attribution invalid: {audit.get('invalid', 0)}", file=sys.stderr)
        print(f"  duplicate ids: {audit.get('duplicate_round_trip_ids', [])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
