import csv
import json
from pathlib import Path

from a_share_agent.backtest.data_integrity import sha256_file
from a_share_agent.backtest.trusted_research_sources import (
    audit_official_trading_calendar,
    audit_trading_rule_provenance,
)
from a_share_agent.backtest.universe_reconciliation import reconcile_universe_snapshot_counts
from a_share_agent.backtest.universe_source_semantics import validate_universe_source_semantics


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_trusted_calendar_is_fail_closed_when_missing(tmp_path: Path):
    audit = audit_official_trading_calendar(
        tmp_path,
        research_start="2026-01-01",
        research_end="2026-01-31",
    )
    assert audit["verified"] is False
    assert audit["trading_dates"] == []


def test_trusted_calendar_requires_physical_source_artifact(tmp_path: Path):
    backtest = tmp_path / "data" / "backtest"
    calendar = backtest / "trusted_trading_calendar.csv"
    _write_csv(calendar, ["date"], [{"date": "2026-01-05"}, {"date": "2026-01-06"}])
    manifest = {
        "source_type": "INDEPENDENT_OFFICIAL_EXPORT",
        "source_dataset_id": "calendar-test",
        "calendar_semantics": "OPEN_DATES_ONLY",
        "calendar_sha256": sha256_file(calendar),
        "row_count": 2,
        "source_files": [
            {
                "source_document_id_or_url": "official-calendar-doc",
                "raw_source_path": "data/backtest/missing-source.txt",
                "raw_source_hash": "a" * 64,
            }
        ],
    }
    (backtest / "trusted_trading_calendar_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    audit = audit_official_trading_calendar(
        tmp_path,
        research_start="2026-01-01",
        research_end="2026-01-31",
    )
    assert audit["verified"] is False
    assert audit["source_artifacts_verified"] is False

    raw = backtest / "calendar-source.txt"
    raw.write_text("official exchange calendar source", encoding="utf-8")
    manifest["source_files"][0]["raw_source_path"] = "data/backtest/calendar-source.txt"
    manifest["source_files"][0]["raw_source_hash"] = sha256_file(raw)
    (backtest / "trusted_trading_calendar_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    audit = audit_official_trading_calendar(
        tmp_path,
        research_start="2026-01-01",
        research_end="2026-01-31",
    )
    assert audit["verified"] is True
    assert audit["trading_dates"] == ["2026-01-05", "2026-01-06"]


def test_trading_rule_provenance_rejects_plausible_hash_without_artifact(tmp_path: Path):
    backtest = tmp_path / "data" / "backtest"
    backtest.mkdir(parents=True)
    manifest = {
        "source_type": "INDEPENDENT_OFFICIAL_RULE_DOCUMENTS",
        "source_dataset_id": "rules-test",
        "rule_checks": {
            "check_a": {
                "rule_reference": "Article 1",
                "source_document_id_or_url": "official-rule-doc",
                "raw_source_path": "data/backtest/missing-rule.txt",
                "raw_source_hash": "b" * 64,
            }
        },
    }
    (backtest / "trading_rules_provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    audit = audit_trading_rule_provenance(tmp_path, ["check_a"])
    assert audit["verified"] is False
    assert audit["invalid_checks"] == ["check_a"]


def test_local_semantics_allow_true_is_not_source_wide_evidence(tmp_path: Path):
    raw = tmp_path / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
    raw.mkdir(parents=True)
    sources = {}
    for name, field in (
        ("szse_delisted_register.csv", "终止上市日期"),
        ("sse_delisted_register.csv", "摘牌日期"),
        ("bse_delisted_register.csv", "终止上市日期"),
    ):
        sources[name] = {
            "raw_date_field": field,
            "normalized_field": "delisting_date",
            "semantic_status": "SOURCE_WIDE_VERIFIED",
            "allow_as_delisting_date": True,
        }
    (raw / "source_semantics.json").write_text(json.dumps({"sources": sources}), encoding="utf-8")
    _write_csv(
        raw / "sse_delisting_date_verification.csv",
        ["symbol", "raw_date", "verified_delisting_date", "verification_status", "official_announcement_url"],
        [{
            "symbol": "600001.SH",
            "raw_date": "2026-01-01",
            "verified_delisting_date": "2026-01-01",
            "verification_status": "MATCH",
            "official_announcement_url": "https://example.invalid/official",
        }],
    )
    audit = validate_universe_source_semantics(raw)
    assert audit["ready"] is False
    assert audit["unverified_source_wide_evidence"]
    assert audit["manifest_binding_audit"]["manifest_binding_present"] is False


def test_universe_reconciliation_requires_snapshot_and_register_hash_integrity(tmp_path: Path):
    master = tmp_path / "security_master.csv"
    _write_csv(
        master,
        ["symbol", "board", "listing_date", "delisting_date", "active_from", "active_to"],
        [{
            "symbol": "600000.SH",
            "board": "SSE_MAIN",
            "listing_date": "1999-01-01",
            "delisting_date": "",
            "active_from": "1999-01-01",
            "active_to": "",
        }],
    )
    snap_dir = tmp_path / "official_universe_snapshots"
    snap = snap_dir / "2026-01-05.csv"
    _write_csv(
        snap,
        ["symbol", "board", "security_type"],
        [{"symbol": "600000.SH", "board": "SSE_MAIN", "security_type": "A_SHARE_COMMON_EQUITY"}],
    )
    raw = snap_dir / "raw_registers" / "sse_main_listing_register.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("official register", encoding="utf-8")
    manifest = {
        "source_registers": {
            "directory": "raw_registers",
            "files": {raw.name: sha256_file(raw)},
        },
        "snapshots": {
            "2026-01-05": {
                "file": "2026-01-05.csv",
                "total_symbols": 1,
                "sha256": sha256_file(snap),
            }
        },
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    audit = reconcile_universe_snapshot_counts(master, snap_dir)
    assert audit["official_snapshot_integrity_ready"] is True
    assert audit["match"] is True

    snap.write_text(
        "symbol,board,security_type\n600001.SH,SSE_MAIN,A_SHARE_COMMON_EQUITY\n",
        encoding="utf-8",
    )
    tampered = reconcile_universe_snapshot_counts(master, snap_dir)
    assert tampered["official_snapshot_integrity_ready"] is False
    assert tampered["match"] is False
