"""Fold-level preflight validation for walk-forward execution.

Validates physical data, calendar coverage, symbol tradability, warmup
sufficiency, and benchmark availability before a fold is considered READY.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import BacktestSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exchange_for_symbol(symbol: str) -> str:
    """Map a symbol to its exchange name (SSE, SZSE, BSE)."""
    sfx = symbol.split(".")[-1].upper() if "." in symbol else "SH"
    if sfx == "SH":
        return "SSE"
    if sfx == "SZ":
        return "SZSE"
    if sfx == "BJ":
        return "BSE"
    return "SSE"


def _read_json(path: Path, ledger: Any = None) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        if ledger is not None:
            data = ledger.read(path, kind="calendar_manifest", parser=lambda raw: json.loads(raw.decode("utf-8")))
            return data if isinstance(data, dict) else {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_csv_dates(path: Path, ledger: Any = None) -> list[str]:
    """Read a calendar CSV that has at least a 'date' column, OPEN_DATES_ONLY semantics."""
    if not path.exists():
        return []
    if ledger is not None:
        rows = ledger.read(path, kind="calendar", parser=lambda raw:
            list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))))
        return [str(row.get("date", "")).strip() for row in rows if row.get("date", "").strip()]
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [str(row.get("date", "")).strip() for row in reader if row.get("date", "").strip()]


def _sha256_file(path: Path, ledger: Any = None) -> str:
    if ledger is not None:
        return hashlib.sha256(ledger.read(path, kind="calendar_hash")).hexdigest()
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _bar_date_count(bars: list[dict[str, Any]], date_set: set[str]) -> int:
    """Count how many bars fall within the given date set."""
    return sum(1 for b in bars if str(b.get("date", "")).strip() in date_set)


def _check_nonfinite(bars: list[dict[str, Any]]) -> list[str]:
    """Return field names with non-finite values found in any bar."""
    numeric_fields = {"open", "high", "low", "close", "volume", "amount"}
    bad: set[str] = set()
    for b in bars:
        for field in numeric_fields:
            v = b.get(field)
            if v is not None:
                try:
                    fv = float(v)
                    if math.isnan(fv) or math.isinf(fv):
                        bad.add(field)
                except (ValueError, TypeError):
                    bad.add(field)
    return sorted(bad)


def _check_malformed(bars: list[dict[str, Any]]) -> list[str]:
    """Return bar indices (as strings) missing critical fields."""
    required = {"date", "open", "high", "low", "close", "volume"}
    bad: list[str] = []
    for i, b in enumerate(bars):
        missing = [k for k in required if k not in b or b[k] is None]
        if missing:
            bad.append(f"bar[{i}]: missing {','.join(missing)}")
    return bad


def _check_duplicates(bars: list[dict[str, Any]]) -> list[str]:
    """Return duplicated date strings."""
    seen: set[str] = set()
    dups: list[str] = []
    for b in bars:
        d = str(b.get("date", "")).strip()
        if d in seen:
            dups.append(d)
        seen.add(d)
    return sorted(set(dups))


def _valid_sha256(value: str) -> bool:
    value = str(value or "").strip().lower()
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


# ---------------------------------------------------------------------------
# Calendar validation
# ---------------------------------------------------------------------------

def _validate_trading_grade_calendar(
    root: Path,
    required_exchanges: set[str],
    warmup_start: str,
    observation_end: str,
    ledger: Any = None,
) -> dict[str, Any]:
    """Validate TradingGrade calendar CSV+manifest. Returns calendar info or blocking reason."""
    cal_dir = root / "data" / "backtest"
    csv_path = cal_dir / "trading_grade_calendar.csv"
    manifest_path = cal_dir / "trading_grade_calendar_manifest.json"

    manifest = _read_json(manifest_path, ledger)
    result: dict[str, Any] = {
        "verified": False,
        "reason": None,
        "source_type": None,
        "schema_version": None,
        "calendar_sha256": None,
        "coverage_start": None,
        "coverage_end": None,
        "weekday_formula": None,
        "exchanges": [],
        "closure_ranges": [],
        "source_files": [],
        "weekday_rule_source": None,
        "trading_dates": [],
        "trading_day_count": 0,
        "exchange_gaps": [],
    }

    if not csv_path.exists():
        if ledger is not None:
            ledger.append(kind="calendar", logical_key=str(csv_path), path=str(csv_path.absolute()),
                source_type="physical", status="missing")
        result["reason"] = "TRADING_GRADE_CALENDAR_CSV_MISSING"
        return result
    if not manifest_path.exists():
        if ledger is not None:
            ledger.append(kind="calendar_manifest", logical_key=str(manifest_path), path=str(manifest_path.absolute()),
                source_type="physical", status="missing")
        result["reason"] = "TRADING_GRADE_CALENDAR_MANIFEST_MISSING"
        return result

    source_type = str(manifest.get("source_type", "")).strip()
    if source_type != "OFFICIAL_NOTICE_DERIVED":
        result["reason"] = f"TRADING_GRADE_CALENDAR_UNEXPECTED_SOURCE_TYPE:{source_type}"
        return result

    schema_version = manifest.get("schema_version")
    if schema_version != 1:
        result["reason"] = f"TRADING_GRADE_CALENDAR_UNEXPECTED_SCHEMA_VERSION:{schema_version}"
        return result

    declared_hash = str(manifest.get("calendar_sha256", "")).strip()
    if not _valid_sha256(declared_hash):
        result["reason"] = "TRADING_GRADE_CALENDAR_INVALID_DECLARED_HASH"
        return result

    actual_hash = _sha256_file(csv_path, ledger)
    if actual_hash != declared_hash:
        result["reason"] = "TRADING_GRADE_CALENDAR_HASH_MISMATCH"
        return result

    weekday_formula = str(manifest.get("weekday_formula", "")).strip()
    if weekday_formula != "MONDAY_FRIDAY_MINUS_CLOSURES":
        result["reason"] = f"TRADING_GRADE_CALENDAR_UNEXPECTED_FORMULA:{weekday_formula}"
        return result

    dates = _read_csv_dates(csv_path, ledger)
    if not dates:
        result["reason"] = "TRADING_GRADE_CALENDAR_EMPTY"
        return result

    exchanges = manifest.get("exchanges", [])
    if not isinstance(exchanges, list):
        exchanges = []

    # Validate per-exchange source coverage
    source_files = manifest.get("source_files", [])
    if not isinstance(source_files, list):
        source_files = []

    exchange_gaps: list[str] = []
    exchange_source_coverage: dict[str, tuple[str, str]] = {}

    for sf in source_files:
        if not isinstance(sf, dict):
            continue
        ex = str(sf.get("exchange", "")).strip()
        cov_start = str(sf.get("coverage_start", "")).strip()
        cov_end = str(sf.get("coverage_end", "")).strip()
        if ex:
            existing = exchange_source_coverage.get(ex)
            if existing:
                cov_start = min(cov_start, existing[0])
                cov_end = max(cov_end, existing[1])
            exchange_source_coverage[ex] = (cov_start, cov_end)

    # If no per-exchange source files, use manifest-level coverage for unified calendar
    if not source_files:
        manifest_cov_start = str(manifest.get("coverage_start", "")).strip()
        manifest_cov_end = str(manifest.get("coverage_end", "")).strip()
        for ex in exchanges:
            exchange_source_coverage[ex] = (manifest_cov_start, manifest_cov_end)

    # Check each required exchange has coverage spanning warmup_start..observation_end
    for ex in sorted(required_exchanges):
        if ex in exchange_source_coverage:
            cov_start, cov_end = exchange_source_coverage[ex]
            if cov_start and cov_end:
                if cov_start > warmup_start:
                    exchange_gaps.append(
                        f"{ex}:source_coverage_starts_{cov_start}_after_warmup_{warmup_start}"
                    )
                if cov_end < observation_end:
                    exchange_gaps.append(
                        f"{ex}:source_coverage_ends_{cov_end}_before_observation_{observation_end}"
                    )
            else:
                exchange_gaps.append(f"{ex}:source_coverage_incomplete")
        elif ex in exchanges:
            exchange_gaps.append(f"{ex}:declared_in_exchanges_but_no_source_file")
        else:
            exchange_gaps.append(f"{ex}:not_covered_by_calendar")

    if exchange_gaps:
        result["reason"] = "EXCHANGE_COVERAGE_GAP:" + ";".join(exchange_gaps)
        result["exchange_gaps"] = exchange_gaps
        return result

    cov_start = str(manifest.get("coverage_start", "")).strip()
    cov_end = str(manifest.get("coverage_end", "")).strip()

    result.update({
        "verified": True,
        "reason": "OK",
        "source_type": source_type,
        "schema_version": schema_version,
        "calendar_sha256": declared_hash,
        "coverage_start": cov_start,
        "coverage_end": cov_end,
        "weekday_formula": weekday_formula,
        "exchanges": exchanges,
        "closure_ranges": manifest.get("closure_ranges", []),
        "source_files": source_files,
        "weekday_rule_source": manifest.get("weekday_rule_source"),
        "trading_dates": dates,
        "trading_day_count": len(dates),
        "exchange_gaps": [],
    })
    return result


# ---------------------------------------------------------------------------
# Symbol / bar validation
# ---------------------------------------------------------------------------

def _validate_symbol_data(
    symbol: str,
    provider: Any,
    warmup_start: str,
    warmup_end_exclusive: str,
    train_start: str,
    test_start: str,
    test_end_exclusive: str,
    observation_end_exclusive: str,
    warmup_bars_required: int,
    calendar_dates_set: set[str],
) -> dict[str, Any]:
    """Validate a single symbol's data readiness for the fold.

    Returns dict with 'ok' (bool) and 'issues' (list[str]).
    """
    issues: list[str] = []

    # --- Check listing date vs warmup ---
    listing_date = provider.listing_date_on(symbol) if hasattr(provider, "listing_date_on") else ""
    warmup_begin = warmup_start
    if listing_date and listing_date > warmup_begin:
        issues.append(f"{symbol}:IPO_listing_{listing_date}_after_warmup_start_{warmup_begin}")

    # --- Get adjusted bars ---
    adj_bars = provider.bars(symbol) if hasattr(provider, "bars") else []
    raw_bars = provider.raw_bars(symbol) if hasattr(provider, "raw_bars") else []

    # --- Adjusted bars validation ---
    if not adj_bars:
        issues.append(f"{symbol}:NO_ADJUSTED_BARS")
    else:
        adj_dups = _check_duplicates(adj_bars)
        if adj_dups:
            issues.append(f"{symbol}:ADJ_DUPLICATE_DATES:{','.join(adj_dups[:5])}")
        adj_nonfinite = _check_nonfinite(adj_bars)
        if adj_nonfinite:
            issues.append(f"{symbol}:ADJ_NONFINITE:{','.join(adj_nonfinite)}")
        adj_malformed = _check_malformed(adj_bars)
        if adj_malformed:
            issues.append(f"{symbol}:ADJ_MALFORMED:{';'.join(adj_malformed[:3])}")

    # --- Raw bars validation ---
    if not raw_bars:
        issues.append(f"{symbol}:NO_RAW_BARS")
    else:
        raw_dups = _check_duplicates(raw_bars)
        if raw_dups:
            issues.append(f"{symbol}:RAW_DUPLICATE_DATES:{','.join(raw_dups[:5])}")
        raw_nonfinite = _check_nonfinite(raw_bars)
        if raw_nonfinite:
            issues.append(f"{symbol}:RAW_NONFINITE:{','.join(raw_nonfinite)}")
        raw_malformed = _check_malformed(raw_bars)
        if raw_malformed:
            issues.append(f"{symbol}:RAW_MALFORMED:{';'.join(raw_malformed[:3])}")

    # --- Warmup sufficiency (on adjusted bars) ---
    warmup_date_set = {d for d in calendar_dates_set if warmup_start <= d < warmup_end_exclusive}
    warmup_adj_count = _bar_date_count(adj_bars, warmup_date_set)
    warmup_raw_count = _bar_date_count(raw_bars, warmup_date_set)

    if warmup_bars_required > 0 and warmup_adj_count < warmup_bars_required:
        issues.append(
            f"{symbol}:INSUFFICIENT_WARMUP_ADJ:{warmup_adj_count}_lt_{warmup_bars_required}"
        )
    if warmup_bars_required > 0 and warmup_raw_count < warmup_bars_required:
        issues.append(
            f"{symbol}:INSUFFICIENT_WARMUP_RAW:{warmup_raw_count}_lt_{warmup_bars_required}"
        )

    # --- Tradability during test window ---
    test_date_set = {d for d in calendar_dates_set if test_start <= d < test_end_exclusive}
    if hasattr(provider, "eligible_on"):
        non_tradable_dates: list[str] = []
        for td in sorted(test_date_set):
            if not provider.eligible_on(symbol, td):
                non_tradable_dates.append(td)
        if non_tradable_dates:
            status_evidence = ""
            if hasattr(provider, "status_on"):
                sample_status = provider.status_on(symbol, non_tradable_dates[0])
                status_evidence = f"status_on={sample_status}"
            ntd_sample = ",".join(non_tradable_dates[:5])
            more = f"+{len(non_tradable_dates)-5}more" if len(non_tradable_dates) > 5 else ""
            issues.append(
                f"{symbol}:NOT_TRADABLE_ON_DATES:{ntd_sample}{more}|{status_evidence}"
            )

    # --- Observation tail coverage ---
    obs_date_set = {
        d for d in calendar_dates_set
        if test_end_exclusive <= d < observation_end_exclusive
    }
    obs_adj_count = _bar_date_count(adj_bars, obs_date_set)
    obs_raw_count = _bar_date_count(raw_bars, obs_date_set)
    obs_expected = len(obs_date_set)

    return {
        "symbol": symbol,
        "exchange": _exchange_for_symbol(symbol),
        "ok": len(issues) == 0,
        "issues": issues,
        "listing_date": listing_date,
        "adj_bars_total": len(adj_bars),
        "raw_bars_total": len(raw_bars),
        "warmup_adj_bars": warmup_adj_count,
        "warmup_raw_bars": warmup_raw_count,
        "warmup_bars_required": warmup_bars_required,
        "obs_adj_bars": obs_adj_count,
        "obs_raw_bars": obs_raw_count,
        "obs_expected_dates": obs_expected,
    }


# ---------------------------------------------------------------------------
# Benchmark validation
# ---------------------------------------------------------------------------

def _validate_benchmark_data(
    benchmark_symbol: str,
    provider: Any,
    warmup_start: str,
    observation_end: str,
    calendar_dates_set: set[str],
) -> dict[str, Any]:
    """Validate a single benchmark symbol's data coverage."""
    issues: list[str] = []
    bars = provider.bars(benchmark_symbol) if hasattr(provider, "bars") else []
    raw_bars = provider.raw_bars(benchmark_symbol) if hasattr(provider, "raw_bars") else []

    if not bars:
        issues.append(f"{benchmark_symbol}:NO_BARS")
    else:
        dups = _check_duplicates(bars)
        if dups:
            issues.append(f"{benchmark_symbol}:DUPLICATE_DATES:{','.join(dups[:5])}")
        nf = _check_nonfinite(bars)
        if nf:
            issues.append(f"{benchmark_symbol}:NONFINITE:{','.join(nf)}")
        mal = _check_malformed(bars)
        if mal:
            issues.append(f"{benchmark_symbol}:MALFORMED:{';'.join(mal[:3])}")

    # Check coverage over full period
    full_date_set = {d for d in calendar_dates_set if warmup_start <= d < observation_end}
    adj_count = _bar_date_count(bars, full_date_set)
    raw_count = _bar_date_count(raw_bars, full_date_set)
    expected = len(full_date_set)

    # Allow up to 1% missing benchmark dates (real providers)
    _coverage_ok = True
    if expected > 0 and adj_count < expected and adj_count < expected * 0.95:
        issues.append(
            f"{benchmark_symbol}:INCOMPLETE_COVERAGE_ADJ:{adj_count}_of_{expected}"
        )
        _coverage_ok = False
    if raw_bars and expected > 0 and raw_count < expected and raw_count < expected * 0.95:
        issues.append(
            f"{benchmark_symbol}:INCOMPLETE_COVERAGE_RAW:{raw_count}_of_{expected}"
        )
        _coverage_ok = False

    return {
        "symbol": benchmark_symbol,
        "ok": len(issues) == 0,
        "issues": issues,
        "adj_bars_total": len(bars),
        "raw_bars_total": len(raw_bars),
        "coverage_adj": adj_count,
        "coverage_raw": raw_count,
        "expected_dates": expected,
    }


# ---------------------------------------------------------------------------
# Context sources availability
# ---------------------------------------------------------------------------

def _gather_context_sources(root: Path) -> dict[str, Any]:
    """Report which context source files exist at the expected paths."""
    expected_files = {
        "security_master": root / "data" / "backtest" / "security_master.csv",
        "status_intervals": root / "data" / "backtest" / "historical_status_intervals.csv",
        "sector_intervals": root / "data" / "backtest" / "historical_sector_intervals.csv",
        "universe_snapshots": root / "data" / "backtest" / "official_universe_snapshots",
        "corporate_actions": root / "data" / "backtest" / "corporate_actions.csv",
        "trading_rules_provenance": root / "data" / "backtest" / "trading_rules_provenance.json",
    }
    result: dict[str, Any] = {}
    for name, path in expected_files.items():
        exists = path.exists()
        result[name] = {
            "exists": exists,
            "path": str(path.relative_to(root)) if exists else None,
        }
        if path.is_dir():
            result[name]["is_dir"] = True
            result[name]["file_count"] = len(list(path.rglob("*"))) if exists else 0
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def preflight_fold(
    provider: Any,
    fold: dict[str, Any],
    settings: BacktestSettings,
    benchmark_symbols: list[str] | None = None,
    calendar_dates: list[str] | None = None,
) -> tuple[str, bool, dict[str, Any], dict[str, Any]]:
    """Validate fold readiness before walk-forward execution.

    Returns (status, complete, reasons, inputs_info).

    status: "READY" or "DATA_BLOCKED"
    complete: True if all checks pass
    reasons: dict with blocking and non-blocking issues per category
    inputs_info: dict with file manifests, coverage stats, calendar info
    """
    reasons: dict[str, Any] = {
        "blocking": {},
        "warnings": {},
    }
    inputs_info: dict[str, Any] = {
        "fold_id": fold.get("fold_id"),
        "observation_end_exclusive": fold.get("observation_end_exclusive"),
    }

    # Extract fold intervals
    warmup_start = str(fold.get("warmup_start", ""))
    warmup_end_exclusive = str(fold.get("warmup_end_exclusive", ""))
    train_start = str(fold.get("train_start", ""))
    train_end_exclusive = str(fold.get("train_end_exclusive", ""))
    test_start = str(fold.get("test_start", ""))
    test_end_exclusive = str(fold.get("test_end_exclusive", ""))
    observation_end_exclusive = str(fold.get("observation_end_exclusive", ""))
    universe = fold.get("universe", [])
    if not isinstance(universe, list):
        universe = []
    warmup_bars_required = max(0, int(getattr(settings, "warmup_bars", 260)))

    # === STEP 1: CALENDAR ===
    trading_dates: list[str] = []

    if calendar_dates is not None and len(calendar_dates) > 0:
        # Use explicitly provided calendar dates
        trading_dates = sorted(set(str(d) for d in calendar_dates if str(d).strip()))
        inputs_info["calendar"] = {
            "source": "EXPLICIT_PARAMETER",
            "trading_day_count": len(trading_dates),
            "first_date": trading_dates[0] if trading_dates else None,
            "last_date": trading_dates[-1] if trading_dates else None,
        }
    else:
        # Try TradingGrade derived calendar
        required_exchanges = {_exchange_for_symbol(sym) for sym in universe}
        cal_result = _validate_trading_grade_calendar(
            Path(provider.root) if hasattr(provider, "root") else Path(),
            required_exchanges,
            warmup_start,
            observation_end_exclusive,
            ledger=getattr(provider, "ledger", None),
        )
        inputs_info["calendar"] = cal_result

        if not cal_result.get("verified"):
            reasons["blocking"]["calendar"] = cal_result.get("reason", "CALENDAR_UNVERIFIED")
            return _result("DATA_BLOCKED", False, reasons, inputs_info)

        trading_dates = cal_result["trading_dates"]
        inputs_info["calendar"]["trading_date_count"] = len(trading_dates)

    if not trading_dates:
        reasons["blocking"]["calendar"] = "NO_TRADING_DATES"
        return _result("DATA_BLOCKED", False, reasons, inputs_info)

    calendar_dates_set = set(trading_dates)

    # Warmup coverage on calendar
    warmup_cal_dates = [d for d in trading_dates if warmup_start <= d < warmup_end_exclusive]
    train_cal_dates = [d for d in trading_dates if train_start <= d < train_end_exclusive]
    test_cal_dates = [d for d in trading_dates if test_start <= d < test_end_exclusive]
    obs_cal_dates = [d for d in trading_dates if test_end_exclusive <= d < observation_end_exclusive]

    inputs_info["calendar_coverage"] = {
        "warmup_trading_days": len(warmup_cal_dates),
        "train_trading_days": len(train_cal_dates),
        "test_trading_days": len(test_cal_dates),
        "observation_trading_days": len(obs_cal_dates),
        "total_trading_days": len(trading_dates),
    }

    # === STEP 2: UNIVERSE WARMUP COVERAGE ===
    inputs_info["warmup_coverage"] = {
        "warmup_start": warmup_start,
        "warmup_end_exclusive": warmup_end_exclusive,
        "calendar_warmup_days": len(warmup_cal_dates),
        "warmup_bars_required": warmup_bars_required,
    }

    if warmup_bars_required > 0 and len(warmup_cal_dates) < warmup_bars_required:
        reasons["blocking"]["warmup_calendar"] = (
            f"INSUFFICIENT_CALENDAR_WARMUP:{len(warmup_cal_dates)}_lt_{warmup_bars_required}"
        )

    # === STEP 3: PER-SYMBOL VALIDATION ===
    symbol_results: dict[str, dict[str, Any]] = {}
    symbol_blockers: list[str] = []
    symbol_warnings: list[str] = []
    symbols_available = 0

    for sym in universe:
        sresult = _validate_symbol_data(
            sym, provider,
            warmup_start, warmup_end_exclusive,
            train_start, test_start, test_end_exclusive,
            observation_end_exclusive,
            warmup_bars_required,
            calendar_dates_set,
        )
        symbol_results[sym] = sresult
        if sresult["ok"]:
            symbols_available += 1
        else:
            for issue in sresult["issues"]:
                if any(kw in issue.upper() for kw in (
                    "NO_RAW_BARS", "NO_ADJUSTED_BARS",
                    "INSUFFICIENT_WARMUP",
                    "IPO_",
                    "NOT_TRADABLE",
                    "DUPLICATE", "NONFINITE", "MALFORMED",
                )):
                    symbol_blockers.append(issue)
                else:
                    symbol_warnings.append(issue)

    universe_size = len(universe)
    inputs_info["universe_coverage"] = {
        "symbols_requested": universe_size,
        "symbols_available": symbols_available,
        "symbols_blocked": universe_size - symbols_available,
        "coverage_ratio": symbols_available / universe_size if universe_size > 0 else 0.0,
    }

    if symbol_blockers:
        reasons["blocking"]["symbols"] = symbol_blockers
    if symbol_warnings:
        reasons["warnings"]["symbols"] = symbol_warnings

    # === STEP 4: RAW / ADJ FILES MANIFEST ===
    raw_files: dict[str, Any] = {}
    adj_files: dict[str, Any] = {}
    for sym, sr in symbol_results.items():
        raw_files[sym] = {
            "bars": sr["raw_bars_total"],
            "warmup_bars": sr["warmup_raw_bars"],
            "obs_bars": sr["obs_raw_bars"],
        }
        adj_files[sym] = {
            "bars": sr["adj_bars_total"],
            "warmup_bars": sr["warmup_adj_bars"],
            "obs_bars": sr["obs_adj_bars"],
        }
    inputs_info["raw_files"] = raw_files
    inputs_info["adj_files"] = adj_files

    # === STEP 5: BENCHMARK VALIDATION ===
    if benchmark_symbols:
        benchmark_symbols = list(dict.fromkeys(str(s) for s in benchmark_symbols))
    else:
        benchmark_symbols = ["000300.SH", "000905.SH", "000016.SH"]

    benchmark_results: dict[str, dict[str, Any]] = {}
    benchmark_blockers: list[str] = []

    for bm in benchmark_symbols:
        bresult = _validate_benchmark_data(
            bm, provider, warmup_start, observation_end_exclusive, calendar_dates_set,
        )
        benchmark_results[bm] = bresult
        if not bresult["ok"]:
            benchmark_blockers.extend(bresult["issues"])

    inputs_info["benchmark_files"] = {
        bm: {
            "adj_bars": br["adj_bars_total"],
            "raw_bars": br["raw_bars_total"],
            "coverage_adj": br["coverage_adj"],
            "coverage_raw": br["coverage_raw"],
            "expected_dates": br["expected_dates"],
            "ok": br["ok"],
        }
        for bm, br in benchmark_results.items()
    }

    if benchmark_blockers:
        reasons["blocking"]["benchmarks"] = benchmark_blockers

    # === STEP 6: CONTEXT SOURCES ===
    root_path = Path(provider.root) if hasattr(provider, "root") else Path()
    context_sources = _gather_context_sources(root_path)
    inputs_info["context_sources"] = context_sources

    # === FINAL ===
    if reasons["blocking"]:
        return _result("DATA_BLOCKED", False, reasons, inputs_info)

    return _result("READY", True, reasons, inputs_info)


def _result(
    status: str,
    complete: bool,
    reasons: dict[str, Any],
    inputs_info: dict[str, Any],
) -> tuple[str, bool, dict[str, Any], dict[str, Any]]:
    return (status, complete, reasons, inputs_info)
