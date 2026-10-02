import csv
import json
from pathlib import Path

from a_share_agent.backtest.data_integrity import sha256_file
from a_share_agent.backtest.preflight_checks import audit_benchmark_calendar, audit_trading_rules_runtime
from a_share_agent.backtest.security_master_integrity import audit_security_master_integrity


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_delisted_fixture(root: Path) -> tuple[Path, Path]:
    master = root / "security_master.csv"
    _write_csv(
        master,
        ["symbol", "board", "listing_date", "delisting_date", "active_from", "active_to"],
        [
            {"symbol": "000003.SZ", "board": "SZSE_MAIN", "listing_date": "1991-01-14", "delisting_date": "2025-06-14", "active_from": "1991-01-14", "active_to": "2025-06-14"},
            {"symbol": "600001.SH", "board": "SSE_MAIN", "listing_date": "1998-01-22", "delisting_date": "2025-06-20", "active_from": "1998-01-22", "active_to": "2025-06-20"},
            {"symbol": "920001.BJ", "board": "BSE", "listing_date": "2022-01-01", "delisting_date": "2025-07-01", "active_from": "2022-01-01", "active_to": "2025-07-01"},
        ],
    )
    snap = root / "official_universe_snapshots"
    raw = snap / "raw_registers"
    _write_csv(
        raw / "szse_delisted_register.csv",
        ["证券代码", "证券简称", "上市日期", "终止上市日期"],
        [{"证券代码": "000003", "证券简称": "A", "上市日期": "1991-01-14", "终止上市日期": "2025-06-14"}],
    )
    _write_csv(
        raw / "sse_delisted_register.csv",
        ["公司代码", "公司简称", "上市日期", "摘牌日期"],
        [{"公司代码": "600001", "公司简称": "B", "上市日期": "1998-01-22", "摘牌日期": "2025-06-20"}],
    )
    _write_csv(
        raw / "bse_delisted_register.csv",
        ["证券代码", "证券简称", "上市日期", "终止上市日期"],
        [{"证券代码": "920001", "证券简称": "C", "上市日期": "2022-01-01", "终止上市日期": "2025-07-01"}],
    )
    hashes = {
        name: sha256_file(raw / name)
        for name in ("szse_delisted_register.csv", "sse_delisted_register.csv", "bse_delisted_register.csv")
    }
    (snap / "manifest.json").write_text(
        json.dumps({"source_registers": {"files": hashes}}), encoding="utf-8"
    )
    (raw / "source_semantics.json").write_text(
        json.dumps(
            {
                "sources": {
                    "szse_delisted_register.csv": {"raw_date_field": "终止上市日期", "normalized_field": "delisting_date", "allow_as_delisting_date": True, "semantic_status": "VERIFIED"},
                    "sse_delisted_register.csv": {"raw_date_field": "摘牌日期", "normalized_field": "delisting_date", "allow_as_delisting_date": True, "semantic_status": "VERIFIED"},
                    "bse_delisted_register.csv": {"raw_date_field": "终止上市日期", "normalized_field": "delisting_date", "allow_as_delisting_date": True, "semantic_status": "VERIFIED"},
                }
            }
        ),
        encoding="utf-8",
    )
    return master, snap


def test_security_master_integrity_proves_survivorship_from_independent_delisted_registers(tmp_path: Path):
    master, snap = _write_delisted_fixture(tmp_path)
    audit = audit_security_master_integrity(
        master,
        snap,
        research_start="2024-10-01",
        research_end="2026-09-30",
        trading_dates=["2024-10-08", "2025-06-20"],
    )
    assert audit["boundary_integrity"] is True
    assert audit["expected_delisted_symbols_in_period"] == 3
    assert audit["missing_delisted_symbols"] == []
    assert audit["delisted_register_complete"] is True
    assert audit["survivorship_bias_protection_ready"] is True


def test_security_master_integrity_fails_when_required_delisted_source_is_missing(tmp_path: Path):
    master, snap = _write_delisted_fixture(tmp_path)
    (snap / "raw_registers" / "bse_delisted_register.csv").unlink()
    audit = audit_security_master_integrity(
        master,
        snap,
        research_start="2024-10-01",
        research_end="2026-09-30",
        trading_dates=["2024-10-08"],
    )
    assert "bse_delisted_register.csv" in audit["unresolved_delisted_sources"]
    assert audit["delisted_register_complete"] is False
    assert audit["survivorship_bias_protection_ready"] is False


def test_security_master_integrity_detects_prelisting_and_postdelisting_leakage(tmp_path: Path):
    master, snap = _write_delisted_fixture(tmp_path)
    rows = list(csv.DictReader(master.open("r", encoding="utf-8-sig")))
    rows[0]["active_from"] = "1990-01-01"
    rows[1]["active_to"] = "2025-06-21"
    _write_csv(master, list(rows[0].keys()), rows)
    audit = audit_security_master_integrity(
        master,
        snap,
        research_start="2024-10-01",
        research_end="2026-09-30",
        trading_dates=["2024-10-08"],
    )
    assert "000003.SZ" in audit["prelisting_leakage_symbols"]
    assert "600001.SH" in audit["post_delisting_leakage_symbols"]
    assert audit["boundary_integrity"] is False


def test_benchmark_calendar_requires_exact_window_and_warmup():
    expected = ["2024-10-08", "2024-10-09"]
    benchmark = ["2024-09-01", "2024-09-02", "2024-10-08", "2024-10-09"]
    audit = audit_benchmark_calendar(
        benchmark,
        expected,
        research_start="2024-10-01",
        research_end="2024-10-09",
        warmup_required=2,
    )
    assert audit["exact_calendar_match"] is True
    assert audit["warmup_ok"] is True
    assert audit["complete"] is True

    missing = audit_benchmark_calendar(
        ["2024-09-01", "2024-09-02", "2024-10-08"],
        expected,
        research_start="2024-10-01",
        research_end="2024-10-09",
        warmup_required=2,
    )
    assert missing["complete"] is False
    assert missing["missing_trading_dates"] == ["2024-10-09"]


def test_trading_rule_runtime_audit_covers_special_periods_and_board_lots():
    audit = audit_trading_rules_runtime()
    assert audit["verified"] is True
    assert audit["passed"] == audit["total"]
    assert audit["failed_checks"] == []
