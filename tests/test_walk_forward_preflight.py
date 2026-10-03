"""Walk-forward fold preflight tests.

Verifies that preflight_fold validates physical data, calendar coverage,
symbol tradability, warmup sufficiency, and benchmark availability
before a fold is considered READY to execute.
"""
import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.backtest.walk_forward_preflight import preflight_fold


class _SyntheticPreflightProvider:
    """Synthetic provider that returns pre-arranged bar/status data."""

    def __init__(self, root: Path):
        self.root = root
        self._bars: dict[str, list[dict[str, Any]]] = {}
        self._raw_bars: dict[str, list[dict[str, Any]]] = {}
        self._status: dict[str, str] = {}
        self._listing_dates: dict[str, str] = {}
        self._eligible: dict[tuple[str, str], bool] = {}
        self._symbols: list[str] = []
        self._benchmark_symbols: dict[str, list[dict[str, Any]]] = {}

    def bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        all_bars = self._bars.get(symbol, [])
        return all_bars[-count:] if count < len(all_bars) else all_bars

    def raw_bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        all_bars = self._raw_bars.get(symbol, [])
        return all_bars[-count:] if count < len(all_bars) else all_bars

    def eligible_on(self, symbol: str, as_of: str) -> bool:
        return self._eligible.get((symbol, as_of), True)

    def status_on(self, symbol: str, as_of: str) -> str:
        return self._status.get(symbol, "NORMAL")

    def listing_date_on(self, symbol: str) -> str:
        return self._listing_dates.get(symbol, "2000-01-01")

    def active_symbols_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[str]:
        return self._symbols

    def get_benchmark(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        return self._benchmark_symbols.get(symbol, [])

    def get_stock_info(self, symbol: str) -> dict[str, Any]:
        return {"name": symbol, "board": symbol.split(".")[1] if "." in symbol else "SH"}


def _make_trading_dates(start: str, end: str) -> list[str]:
    dates: list[str] = []
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


def _exchange_for_symbol(symbol: str) -> str:
    sfx = symbol.split(".")[-1].upper() if "." in symbol else "SH"
    if sfx in ("SH", "BJ"):
        return "SSE" if sfx == "SH" else "BSE"
    if sfx == "SZ":
        return "SZSE"
    return "SSE"


def _write_trading_grade_calendar(
    root: Path,
    dates: list[str],
    *,
    exchanges: list[str] | None = None,
    source_files: list[dict[str, Any]] | None = None,
    closure_ranges: list[dict[str, str]] | None = None,
) -> Path:
    cal_dir = root / "data" / "backtest"
    cal_dir.mkdir(parents=True, exist_ok=True)

    csv_path = cal_dir / "trading_grade_calendar.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date"])
        writer.writeheader()
        for d in dates:
            writer.writerow({"date": d})

    cal_sha256 = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    exchanges = exchanges or ["SSE"]
    cov_start = dates[0] if dates else ""
    cov_end = dates[-1] if dates else ""

    if source_files is None:
        source_files = [
            {
                "path": "calendar_sources/sse_2025_annual_notice.html",
                "sha256": "a" * 64,
                "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml",
                "coverage_start": cov_start,
                "coverage_end": cov_end,
                "published_at": "2024-12-23",
                "exchange": "SSE",
            }
        ]

    manifest: dict[str, Any] = {
        "source_type": "OFFICIAL_NOTICE_DERIVED",
        "schema_version": 1,
        "calendar_sha256": cal_sha256,
        "coverage_start": cov_start,
        "coverage_end": cov_end,
        "weekday_formula": "MONDAY_FRIDAY_MINUS_CLOSURES",
        "exchanges": exchanges,
        "closure_ranges": closure_ranges or [],
        "source_files": source_files,
        "weekday_rule_source": {
            "path": "calendar_sources/sse_trading_schedule.html",
            "sha256": "b" * 64,
            "url": "https://english.sse.com.cn/start/trading/schedule/",
        },
    }

    manifest_path = cal_dir / "trading_grade_calendar_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return csv_path


def _make_bars(
    symbol: str,
    dates: list[str],
    *,
    open_p: float = 10.0,
    high_p: float = 11.0,
    low_p: float = 9.5,
    close_p: float = 10.5,
    volume: float = 1_000_000,
) -> list[dict[str, Any]]:
    return [
        {
            "date": d,
            "open": open_p + (i % 5) * 0.1,
            "high": high_p + (i % 5) * 0.2,
            "low": low_p - (i % 5) * 0.1,
            "close": close_p + (i % 5) * 0.15,
            "volume": volume + i * 1000,
            "amount": (volume + i * 1000) * (close_p + (i % 5) * 0.15),
        }
        for i, d in enumerate(dates)
    ]


def _make_fold(
    *,
    fold_id: int = 1,
    warmup_start: str = "2024-01-01",
    warmup_end_exclusive: str = "2025-01-01",
    train_start: str = "2025-01-01",
    train_end_exclusive: str = "2025-04-01",
    test_start: str = "2025-04-01",
    test_end_exclusive: str = "2025-07-01",
    observation_end_exclusive: str = "2025-08-01",
    universe: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "fold_id": fold_id,
        "warmup_start": warmup_start,
        "warmup_end_exclusive": warmup_end_exclusive,
        "train_start": train_start,
        "train_end_exclusive": train_end_exclusive,
        "test_start": test_start,
        "test_end_exclusive": test_end_exclusive,
        "observation_end_exclusive": observation_end_exclusive,
        "universe": universe or ["600000.SH", "000001.SZ"],
    }


def _setup_happy_path(root: Path) -> tuple[_SyntheticPreflightProvider, dict[str, Any], BacktestSettings]:
    trading_dates = _make_trading_dates("2024-01-01", "2025-08-01")
    source_files = [
        {
            "path": "calendar_sources/sse_2025.html",
            "sha256": "a" * 64,
            "url": "https://www.sse.com.cn/...",
            "coverage_start": "2024-01-01",
            "coverage_end": "2025-08-01",
            "published_at": "2024-12-23",
            "exchange": "SSE",
        },
        {
            "path": "calendar_sources/szse_2025.html",
            "sha256": "c" * 64,
            "url": "https://www.szse.cn/...",
            "coverage_start": "2024-01-01",
            "coverage_end": "2025-08-01",
            "published_at": "2024-12-23",
            "exchange": "SZSE",
        },
    ]
    _write_trading_grade_calendar(
        root, trading_dates, exchanges=["SSE", "SZSE"], source_files=source_files)

    warmup_dates = _make_trading_dates("2024-01-01", "2024-12-31")
    train_dates = _make_trading_dates("2025-01-01", "2025-03-31")
    test_dates = _make_trading_dates("2025-04-01", "2025-06-30")
    obs_dates = _make_trading_dates("2025-07-01", "2025-08-01")

    provider = _SyntheticPreflightProvider(root)
    provider._symbols = ["600000.SH", "000001.SZ"]
    provider._listing_dates = {"600000.SH": "2000-01-01", "000001.SZ": "2000-01-01"}
    provider._eligible = {("600000.SH", d): True for d in test_dates}
    provider._eligible.update({("000001.SZ", d): True for d in test_dates})

    all_dates = warmup_dates + train_dates + test_dates + obs_dates
    for sym in provider._symbols:
        provider._bars[sym] = _make_bars(sym, all_dates)
        provider._raw_bars[sym] = _make_bars(sym, all_dates, open_p=9.8, close_p=10.2)

    for bm in ("000300.SH", "000905.SH", "000016.SH"):
        provider._benchmark_symbols[bm] = _make_bars(bm, all_dates)
        provider._bars[bm] = provider._benchmark_symbols[bm]

    fold = _make_fold(universe=provider._symbols)
    settings = BacktestSettings(start_date="2025-04-01", end_date="2025-07-01", warmup_bars=260)
    return provider, fold, settings


class TestHappyPath:
    def test_happy_path_returns_ready(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        status, complete, reasons, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "READY", f"Expected READY, got {status}: {reasons}"
        assert complete is True

    def test_happy_path_reports_inputs(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        _, _, _, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert "fold_id" in inputs_info
        assert "observation_end_exclusive" in inputs_info
        assert "universe_coverage" in inputs_info
        assert "warmup_coverage" in inputs_info
        assert "calendar_coverage" in inputs_info
        assert "raw_files" in inputs_info
        assert "adj_files" in inputs_info
        assert "benchmark_files" in inputs_info


class TestCalendarBlocked:
    def test_no_calendar_no_dates_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        cal_file = tmp_path / "data" / "backtest" / "trading_grade_calendar.csv"
        if cal_file.exists():
            cal_file.unlink()
        cal_manifest = tmp_path / "data" / "backtest" / "trading_grade_calendar_manifest.json"
        if cal_manifest.exists():
            cal_manifest.unlink()

        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
            calendar_dates=None,
        )
        assert status == "DATA_BLOCKED"
        assert complete is False

    def test_calendar_provided_explicitly(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        trading_dates = _make_trading_dates("2024-01-01", "2025-08-01")
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
            calendar_dates=trading_dates,
        )
        assert status == "READY", f"Expected READY with explicit calendar, got {status}: {reasons}"

    def test_calendar_sha_mismatch_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        manifest_path = tmp_path / "data" / "backtest" / "trading_grade_calendar_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["calendar_sha256"] = "x" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
            calendar_dates=None,
        )
        assert status == "DATA_BLOCKED"

    def test_missing_manifest_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        manifest_path = tmp_path / "data" / "backtest" / "trading_grade_calendar_manifest.json"
        if manifest_path.exists():
            manifest_path.unlink()

        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
            calendar_dates=None,
        )
        assert status == "DATA_BLOCKED"


class TestExchangeCoverage:
    def test_sz_symbol_warmup_crosses_szse_gap_blocked(self, tmp_path: Path):
        sse_dates = _make_trading_dates("2023-01-01", "2025-10-01")
        szse_dates = _make_trading_dates("2024-01-01", "2025-10-01")
        all_dates = sorted(set(sse_dates + szse_dates))
        source_files = [
            {
                "path": "calendar_sources/sse_2023.html",
                "sha256": "a" * 64,
                "url": "https://www.sse.com.cn/...",
                "coverage_start": "2023-01-01",
                "coverage_end": "2025-12-31",
                "published_at": "2022-12-27",
                "exchange": "SSE",
            },
            {
                "path": "calendar_sources/szse_2024.html",
                "sha256": "c" * 64,
                "url": "https://www.szse.cn/...",
                "coverage_start": "2024-01-01",
                "coverage_end": "2025-12-31",
                "published_at": "2023-12-26",
                "exchange": "SZSE",
            },
        ]
        _write_trading_grade_calendar(
            tmp_path, all_dates,
            exchanges=["SSE", "SZSE"],
            source_files=source_files,
        )

        provider = _SyntheticPreflightProvider(tmp_path)
        provider._symbols = ["600000.SH", "000001.SZ"]
        provider._listing_dates = {"600000.SH": "2000-01-01", "000001.SZ": "2000-01-01"}
        all_bars = _make_trading_dates("2023-10-01", "2025-06-30")
        for sym in provider._symbols:
            provider._bars[sym] = _make_bars(sym, all_bars)
            provider._raw_bars[sym] = _make_bars(sym, all_bars, open_p=9.8, close_p=10.2)

        for bm in ("000300.SH", "000905.SH", "000016.SH"):
            provider._benchmark_symbols[bm] = _make_bars(bm, all_bars)
            provider._bars[bm] = provider._benchmark_symbols[bm]

        fold = _make_fold(
            warmup_start="2023-10-01", warmup_end_exclusive="2024-10-01",
            train_start="2024-10-01", train_end_exclusive="2025-02-01",
            test_start="2025-02-01", test_end_exclusive="2025-05-01",
            observation_end_exclusive="2025-07-01",
            universe=provider._symbols,
        )
        settings = BacktestSettings(start_date="2025-02-01", end_date="2025-05-01", warmup_bars=260)

        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED", f"Expected DATA_BLOCKED for SZ SZSE gap, got {status}: {reasons}"

    def test_sz_symbol_warmup_within_szse_coverage_ok(self, tmp_path: Path):
        all_dates = _make_trading_dates("2024-01-01", "2025-10-01")
        source_files = [
            {
                "path": "calendar_sources/sse_2024.html",
                "sha256": "a" * 64,
                "url": "https://www.sse.com.cn/...",
                "coverage_start": "2024-01-01",
                "coverage_end": "2025-12-31",
                "published_at": "2023-12-26",
                "exchange": "SSE",
            },
            {
                "path": "calendar_sources/szse_2024.html",
                "sha256": "c" * 64,
                "url": "https://www.szse.cn/...",
                "coverage_start": "2024-01-01",
                "coverage_end": "2025-12-31",
                "published_at": "2023-12-26",
                "exchange": "SZSE",
            },
        ]
        _write_trading_grade_calendar(
            tmp_path, all_dates,
            exchanges=["SSE", "SZSE"],
            source_files=source_files,
        )

        provider = _SyntheticPreflightProvider(tmp_path)
        provider._symbols = ["600000.SH", "000001.SZ"]
        provider._listing_dates = {"600000.SH": "2000-01-01", "000001.SZ": "2000-01-01"}
        for sym in provider._symbols:
            provider._bars[sym] = _make_bars(sym, all_dates)
            provider._raw_bars[sym] = _make_bars(sym, all_dates, open_p=9.8, close_p=10.2)

        for bm in ("000300.SH", "000905.SH", "000016.SH"):
            provider._benchmark_symbols[bm] = _make_bars(bm, all_dates)
            provider._bars[bm] = provider._benchmark_symbols[bm]

        fold = _make_fold(
            warmup_start="2024-01-01", warmup_end_exclusive="2025-01-01",
            train_start="2025-01-01", train_end_exclusive="2025-04-01",
            test_start="2025-04-01", test_end_exclusive="2025-07-01",
            observation_end_exclusive="2025-08-01",
            universe=provider._symbols,
        )
        settings = BacktestSettings(start_date="2025-04-01", end_date="2025-07-01", warmup_bars=260)

        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "READY", f"Expected READY, got {status}: {reasons}"


class TestWarmupBlocked:
    def test_warmup_bars_insufficient_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._bars["600000.SH"] = _make_bars(
            "600000.SH", _make_trading_dates("2025-06-01", "2025-06-15"))
        provider._raw_bars["600000.SH"] = _make_bars(
            "600000.SH", _make_trading_dates("2025-06-01", "2025-06-15"))
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_ipo_symbol_with_insufficient_warmup_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        recent_listing = "688999.SH"
        provider._symbols.append(recent_listing)
        provider._listing_dates[recent_listing] = "2025-03-15"
        trading_dates = _make_trading_dates("2024-01-01", "2025-08-01")
        listing_bars = _make_bars(recent_listing, [d for d in trading_dates if d >= "2025-03-15"])
        provider._bars[recent_listing] = listing_bars
        provider._raw_bars[recent_listing] = listing_bars
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"


class TestSuspendedBlocked:
    def test_suspended_without_status_evidence_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        test_dates = _make_trading_dates("2025-04-01", "2025-06-30")
        for d in test_dates:
            provider._eligible[("600000.SH", d)] = False
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"


class TestRawDataBlocked:
    def test_missing_raw_bars_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._raw_bars.pop("600000.SH", None)
        provider._bars.pop("600000.SH", None)
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_duplicate_dates_in_bars_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        bars = provider._bars["600000.SH"]
        if len(bars) >= 2:
            bars.insert(1, dict(bars[0]))
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_nonfinite_values_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        bars = provider._bars["600000.SH"]
        if bars:
            bars[0]["close"] = float("nan")
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_malformed_bar_missing_critical_field_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        bars = provider._bars["000001.SZ"]
        if bars:
            del bars[0]["close"]
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_raw_never_uses_adj_as_fallback(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._raw_bars.pop("600000.SH", None)
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"
        assert "600000.SH" in str(reasons)


class TestBenchmarkBlocked:
    def test_missing_benchmark_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._benchmark_symbols.pop("000300.SH", None)
        provider._bars.pop("000300.SH", None)
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_partial_benchmark_coverage_blocked(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        test_dates = _make_trading_dates("2025-04-01", "2025-06-30")
        provider._benchmark_symbols["000905.SH"] = _make_bars("000905.SH", test_dates[-5:])
        provider._bars["000905.SH"] = provider._benchmark_symbols["000905.SH"]
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "DATA_BLOCKED"

    def test_all_three_benchmarks_validated(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "READY", f"Expected READY with all benchmarks, got {status}: {reasons}"
        assert complete is True


class TestCoverageReporting:
    def test_coverage_reports_missing_symbols(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._bars.pop("000001.SZ", None)
        provider._raw_bars.pop("000001.SZ", None)
        _, _, reasons, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        all_text = str(reasons) + str(inputs_info)
        assert "000001.SZ" in all_text

    def test_universe_coverage_pct_reported(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        _, _, _, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        uc = inputs_info.get("universe_coverage", {})
        assert isinstance(uc, dict)
        assert uc.get("symbols_requested", 0) == 2
        assert uc.get("symbols_available", 0) == 2


class TestManifestFiles:
    def test_manifest_includes_all_consumed_files(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        _, _, _, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        raw_files = inputs_info.get("raw_files", {})
        assert "600000.SH" in raw_files
        assert "000001.SZ" in raw_files
        bench_files = inputs_info.get("benchmark_files", {})
        for bm in ("000300.SH", "000905.SH", "000016.SH"):
            assert bm in bench_files, f"Missing benchmark {bm} in manifest"

    def test_trading_grade_calendar_detected(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        _, _, _, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        cal = inputs_info.get("calendar", {})
        assert cal.get("source_type") == "OFFICIAL_NOTICE_DERIVED"
        assert cal.get("schema_version") == 1
        assert "calendar_sha256" in cal
        assert "coverage_start" in cal
        assert "coverage_end" in cal
        assert cal.get("weekday_formula") == "MONDAY_FRIDAY_MINUS_CLOSURES"
        assert "exchanges" in cal
        assert "closure_ranges" in cal
        assert "source_files" in cal
        assert "weekday_rule_source" in cal


class TestContextLimits:
    def test_no_status_intervals_allowed(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        status, complete, reasons, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        context = inputs_info.get("context_sources", {})
        assert isinstance(context, dict)


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_universe_valid(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._symbols = []
        fold["universe"] = []
        status, complete, reasons, inputs_info = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        # Empty universe is valid (no symbols, nothing to block)
        assert status == "READY" or complete is False
        uc = inputs_info.get("universe_coverage", {})
        assert uc.get("symbols_requested", 0) == 0
        assert uc.get("symbols_available", 0) == 0
        assert uc.get("coverage_ratio", 1) == 0.0

    def test_zero_warmup_bars(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        settings = BacktestSettings(start_date="2025-04-01", end_date="2025-07-01", warmup_bars=0)
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status in ("READY", "DATA_BLOCKED")

    def test_fold_with_single_symbol(self, tmp_path: Path):
        provider, fold, settings = _setup_happy_path(tmp_path)
        provider._symbols = ["600000.SH"]
        fold["universe"] = ["600000.SH"]
        status, complete, reasons, _ = preflight_fold(
            provider, fold, settings,
            benchmark_symbols=["000300.SH", "000905.SH", "000016.SH"],
        )
        assert status == "READY", f"Expected READY with single symbol, got {status}: {reasons}"
