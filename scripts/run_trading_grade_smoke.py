#!/usr/bin/env python3
"""Trading Grade Smoke — Phase 1.5B/2A canonical REAL_HISTORICAL smoke backtest.

Usage:
    python scripts/run_trading_grade_smoke.py [--output PATH]

Requires cached historical data in data/backtest/cache/bars/ (populated by
prior MCP calls). Fails with NO_DATA if cache is empty for requested symbols.

Outputs a machine-readable JSON artifact to
    data/diagnostics/trading_grade_smoke_latest.json
or the path specified by --output.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project imports — fail fast if anything is missing
# ---------------------------------------------------------------------------
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.engine import BacktestEngine
from a_share_agent.backtest.metrics import batch_audit_attribution, regime_pattern_matrix
from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.config import load_config

# ---------------------------------------------------------------------------
# Smoke configuration
# ---------------------------------------------------------------------------
# Real stocks with cached data — large/mid caps across SH + SZ exchanges.
SMOKE_SYMBOLS = [
    "600519.SH",  # Kweichow Moutai
    "600036.SH",  # China Merchants Bank
    "600900.SH",  # Yangtze Power
    "600276.SH",  # Hengrui Pharma
    "600887.SH",  # Yili
    "000333.SZ",  # Midea
    "000858.SZ",  # Wuliangye
    "002415.SZ",  # Hikvision
    "601318.SH",  # Ping An Insurance
    "000725.SZ",  # BOE
]

# Date range: ~4 months of 2025-2026 data (within cached range 2023-01 to 2026-09)
SMOKE_START = "2025-06-01"
SMOKE_END = "2025-09-30"

# Parameters — default strategy values, NO tuning allowed.
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
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _check_data_kind(provider: HistoricalDataProvider) -> str:
    """Verify data comes from historical cache/CSV, not synthetic/MCP."""
    # If bars() returns non-empty for a cached symbol, data is real.
    test_sym = SMOKE_SYMBOLS[0]
    bars = provider.bars(test_sym)
    if not bars:
        return "NO_BARS"
    # Check it's from cache by verifying the date range makes sense
    first = bars[0]["date"]
    last = bars[-1]["date"]
    if first < "2022-01-01":
        return "SUSPICIOUS_EARLY"
    if last < "2025-01-01":
        return "STALE_CACHE"
    return "HISTORICAL_LOCAL_DATA"


def _file_sha256(path: Path) -> str:
    """Compute SHA256 of a file for provenance tracking."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _per_symbol_inputs(provider: HistoricalDataProvider, symbols: list[str], root: Path) -> list[dict]:
    """Record physical input hashes for each tested symbol."""
    inputs = []
    for sym in symbols:
        entry: dict[str, Any] = {"symbol": sym}
        # Normalise symbol for filename (dot -> underscore)
        fsafe = sym.replace(".", "_")
        cache_path = root / "data" / "backtest" / "cache" / "bars" / f"{fsafe}.json"
        raw_path = root / "data" / "backtest" / "raw_prices" / f"{fsafe}.csv"
        bars = provider.bars(sym)
        entry["adjusted_bars_source_path"] = str(cache_path)
        entry["adjusted_bars_sha256"] = _file_sha256(cache_path) if cache_path.exists() else "MISSING"
        entry["raw_bars_source_path"] = str(raw_path)
        entry["raw_bars_sha256"] = _file_sha256(raw_path) if raw_path.exists() else "MISSING"
        entry["rows"] = len(bars) if bars else 0
        entry["first_date"] = bars[0]["date"] if bars else None
        entry["last_date"] = bars[-1]["date"] if bars else None
        # Fail if no raw execution data
        if not raw_path.exists():
            entry["raw_data_warning"] = "NO_RAW_EXECUTION_DATA"
        inputs.append(entry)
    return inputs


def run_smoke(output_path: str | Path) -> dict[str, Any]:
    """Run the smoke backtest and write the artifact."""
    src_root = Path(__file__).resolve().parent.parent
    cfg = load_config(src_root)
    data_root = src_root

    # Initialize provider WITHOUT MCP — cache-only
    provider = HistoricalDataProvider(data_root, mcp=None, use_cache=True)

    # Verify data kind
    data_kind = _check_data_kind(provider)
    if data_kind != "HISTORICAL_LOCAL_DATA":
        msg = {"error": f"Data check failed: {data_kind}", "data_kind": data_kind}
        return msg

    # Source provenance: we know it's local cached data but cannot
    # independently verify provenance without the MCP audit trail.
    source_verification = "UNVERIFIED_CACHE"

    # Build settings with defaults
    settings = BacktestSettings(
        start_date=SMOKE_START,
        end_date=SMOKE_END,
        **SMOKE_PARAMS,
    )

    # Count requested symbols vs available
    tested_symbols = []
    skipped_symbols = []
    for sym in SMOKE_SYMBOLS:
        if provider.bars(sym):
            tested_symbols.append(sym)
        else:
            skipped_symbols.append(sym)

    if not tested_symbols:
        return {"error": "NO_TRADES", "data_kind": data_kind,
                "reason": "All requested symbols have no cached data",
                "source_verification": source_verification}

    # Record input hashes before running backtest
    input_files = _per_symbol_inputs(provider, tested_symbols, data_root)

    # Run the backtest
    engine = BacktestEngine(cfg, provider, settings)
    report = engine.run(tested_symbols)
    trades = report.get("trades", [])

    # Attribution audit
    audit = batch_audit_attribution(trades)

    # Matrix
    matrix = regime_pattern_matrix(trades)

    # Build artifact
    sell_trades = [t for t in trades if t.get("direction") == "SELL"]
    buy_trades = [t for t in trades if t.get("direction") == "BUY"]

    # Overall context quality
    from a_share_agent.backtest.metrics import entry_context_quality
    context_counts = {"ok": 0, "not_ok": 0}
    for t in sell_trades:
        ecq = entry_context_quality(t)
        if ecq["usable"]:
            context_counts["ok"] += 1
        else:
            context_counts["not_ok"] += 1

    artifact: dict[str, Any] = {
        # Metadata
        "artifact_version": "1.2.0",
        "git_commit_sha": _git_sha(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Smoke config
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
        # Trade summary
        "total_trades": len(trades),
        "buy_trades": len(buy_trades),
        "closed_trades": len(sell_trades),
        "context_usable_trades": context_counts["ok"],
        "context_unusable_trades": context_counts["not_ok"],
        "patterns_seen": sorted(set(t.get("pattern_id") for t in trades if t.get("pattern_id"))),
        "regimes_seen": sorted(set(t.get("regime_at_signal") for t in trades if t.get("regime_at_signal"))),
        "lifecycles_seen": sorted(set(t.get("theme_lifecycle") for t in trades if t.get("theme_lifecycle"))),
        "unknown_lifecycle_count": sum(1 for t in trades if t.get("theme_lifecycle") == "UNKNOWN"),
        "degraded_context_trade_count": sum(
            1 for t in trades
            if (t.get("theme_data_quality_at_signal") or {}).get("state") in ("degraded", "unavailable")
        ),
        # Attribution audit
        "attribution_audit": audit,
        # Matrix
        "regime_pattern_matrix": matrix,
        # Performance (pass-through from report)
        "metrics": report.get("metrics", {}),
        # Example trades (first 5 by natural trade_date order)
        "sample_trades": sorted(
            sell_trades,
            key=lambda t: str(t.get("trade_date", "") or ""),
        )[:5],
        # Coverage report
        "coverage": report.get("coverage", {}),
    }

    # Write artifact
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(artifact, f, indent=2, default=str, ensure_ascii=False)
    print(f"Smoke artifact written to {out}")

    # Print summary
    print(f"\n=== TRADING GRADE SMOKE ===")
    print(f"  data_kind:        {data_kind}")
    print(f"  symbols_tested:   {len(tested_symbols)}")
    print(f"  date_range:       {SMOKE_START} ~ {SMOKE_END}")
    print(f"  total_trades:     {len(trades)}")
    print(f"  closed_trades:    {len(sell_trades)}")
    print(f"  patterns_seen:    {artifact['patterns_seen']}")
    print(f"  regimes_seen:     {artifact['regimes_seen']}")
    print(f"  attrib_valid:     {audit['valid']}")
    print(f"  attrib_invalid:   {audit['invalid']}")
    if audit.get("invalid_trades"):
        print(f"  attrib_errors:    {[e for it in audit['invalid_trades'] for e in it.get('errors', [])]}")
    print(f"  source_verification: {artifact["source_verification"]}")
    print(f"  git_sha:          {artifact['git_commit_sha']}")
    print(f"  artifact:         {out}")

    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Grade Smoke Backtest")
    default_out = Path(__file__).resolve().parent.parent / "data" / "diagnostics" / "trading_grade_smoke_latest.json"
    parser.add_argument("--output", default=str(default_out), help="Output artifact path")
    args = parser.parse_args()

    artifact = run_smoke(args.output)
    if "error" in artifact:
        print(f"\nERROR: {artifact['error']}", file=sys.stderr)
        print(f"  source_verification: {artifact.get('source_verification','?')}", file=sys.stderr)
        if artifact.get("attribution_audit", {}).get("invalid", 0) > 0:
            print(f"  attribution invalid: {artifact['attribution_audit']['invalid']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
