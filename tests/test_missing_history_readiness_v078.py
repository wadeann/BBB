import csv
import json
from pathlib import Path

from a_share_agent.backtest.data_integrity import raw_dataset_fingerprint, sha256_file
from a_share_agent.backtest.missing_history_readiness import (
    assess_missing_history_candidate,
    audit_missing_history_candidates,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _candidate() -> dict[str, str]:
    return {
        "symbol": "000584.SZ",
        "listing_date": "1995-11-28",
        "raw_delisting_date": "2025-07-11",
        "source_file": "szse_delisted_register.csv",
        # These generated fields are deliberately not trusted as evidence by the audit.
        "raw_record_found": "True",
        "source_allows_delisting_date": "True",
        "candidate_status": "DATE_PROVENANCE_READY_MASTER_INSERT_NOT_YET_APPROVED",
    }


def _write_membership_sources(root: Path, *, allow: bool = True) -> None:
    snap = root / "data" / "backtest" / "official_universe_snapshots"
    raw = snap / "raw_registers"
    raw.mkdir(parents=True, exist_ok=True)
    register = raw / "szse_delisted_register.csv"
    _write_csv(
        register,
        ["证券代码", "证券简称", "上市日期", "终止上市日期"],
        [{"证券代码": "000584", "证券简称": "工智退", "上市日期": "1995-11-28", "终止上市日期": "2025-07-11"}],
    )
    (snap / "manifest.json").write_text(
        json.dumps({"source_registers": {"files": {register.name: sha256_file(register)}}}),
        encoding="utf-8",
    )
    (raw / "source_semantics.json").write_text(
        json.dumps(
            {
                "sources": {
                    register.name: {
                        "raw_date_field": "终止上市日期",
                        "normalized_field": "delisting_date",
                        "semantic_status": "EXPLICIT_TERMINATION_FIELD" if allow else "BLOCKED",
                        "allow_as_delisting_date": allow,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_raw_dataset(root: Path, raw_dates: list[str]) -> None:
    raw_dir = root / "data" / "backtest" / "raw_prices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / "000584_SZ.csv"
    lines = ["date,open,high,low,close,volume"] + [f"{d},1,1,1,1,1" for d in raw_dates]
    raw_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fp = raw_dataset_fingerprint(raw_dir)
    (root / "raw_dataset_manifest.json").write_text(
        json.dumps(
            {
                "date_range": {"start": "2024-10-08", "end": "2024-10-09"},
                "summary": {
                    "file_count": fp["file_count"],
                    "row_count": fp["row_count"],
                    "overall_dataset_hash": fp["overall_dataset_hash"],
                },
                "file_hashes": fp["file_hashes"],
            }
        ),
        encoding="utf-8",
    )
    coverage = root / "daily_raw_coverage.csv"
    coverage.write_text(
        "date,active_raw_coverage\n2024-10-08,100%\n2024-10-09,100%\n",
        encoding="utf-8",
    )
    (root / "daily_raw_coverage_manifest.json").write_text(
        json.dumps(
            {
                "source_raw_dataset_hash": fp["overall_dataset_hash"],
                "coverage_csv_sha256": sha256_file(coverage),
            }
        ),
        encoding="utf-8",
    )


def _write_interval_source(
    root: Path,
    *,
    kind: str,
    effective_from: str = "2024-10-08",
    effective_to: str = "2024-10-09",
    include_artifact: bool = True,
) -> None:
    bt = root / "data" / "backtest"
    source_id = f"{kind.upper()}_DOC_1"
    artifact_rel = f"data/backtest/provenance_raw/{kind}-source.txt"
    artifact = root / artifact_rel
    if include_artifact:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"official {kind} source", encoding="utf-8")
        digest = sha256_file(artifact)
    else:
        digest = "1" * 64

    if kind == "status":
        _write_csv(
            bt / "historical_status_intervals.csv",
            ["symbol", "status", "effective_from", "effective_to", "source"],
            [{"symbol": "000584.SZ", "status": "TRADABLE", "effective_from": effective_from, "effective_to": effective_to, "source": source_id}],
        )
        sidecar = bt / "status_provenance.csv"
    else:
        _write_csv(
            bt / "historical_sector_intervals.csv",
            ["symbol", "sector_code", "effective_from", "effective_to", "source"],
            [{"symbol": "000584.SZ", "sector_code": "S1", "effective_from": effective_from, "effective_to": effective_to, "source": source_id}],
        )
        sidecar = bt / "sector_provenance.csv"
    _write_csv(
        sidecar,
        ["source_id", "source_document_id_or_url", "raw_source_hash", "raw_source_path"],
        [{"source_id": source_id, "source_document_id_or_url": f"doc-{kind}", "raw_source_hash": digest, "raw_source_path": artifact_rel}],
    )


def _complete_fixture(root: Path, raw_dates: list[str] | None = None) -> None:
    _write_membership_sources(root)
    _write_raw_dataset(root, raw_dates or ["2024-10-08", "2024-10-09"])
    _write_interval_source(root, kind="status")
    _write_interval_source(root, kind="sector")


def test_membership_ready_does_not_imply_execution_ready(tmp_path: Path):
    _write_membership_sources(tmp_path)
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["membership_ready"] is True
    assert result["execution_ready"] is False
    assert "RAW_OHLCV_NOT_MANIFEST_VERIFIED" in result["blockers"]
    assert "HISTORICAL_STATUS_PROVENANCE_INCOMPLETE" in result["blockers"]
    assert "HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE" in result["blockers"]


def test_candidate_claims_cannot_override_current_source_semantics(tmp_path: Path):
    _write_membership_sources(tmp_path, allow=False)
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["membership_source_record_found"] is True
    assert result["membership_source_allows_delisting_date"] is False
    assert result["membership_ready"] is False
    assert "PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE" in result["blockers"]


def test_execution_ready_requires_complete_window_and_real_provenance_artifacts(tmp_path: Path):
    _complete_fixture(tmp_path)
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["membership_ready"] is True
    assert result["raw_dataset_hash_match"] is True
    assert result["daily_raw_coverage_fresh"] is True
    assert result["raw_file_hash_match"] is True
    assert result["raw_window_complete"] is True
    assert result["status_provenance_verified"] is True
    assert result["status_window_coverage_complete"] is True
    assert result["sector_provenance_verified"] is True
    assert result["sector_window_coverage_complete"] is True
    assert result["execution_ready"] is True
    assert result["blockers"] == []


def test_one_hash_bound_raw_bar_cannot_make_execution_ready(tmp_path: Path):
    _complete_fixture(tmp_path, raw_dates=["2024-10-08"])
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["raw_file_hash_match"] is True
    assert result["raw_expected_trading_days"] == 2
    assert result["raw_missing_trading_days"] == 1
    assert result["raw_window_complete"] is False
    assert result["execution_ready"] is False
    assert "RAW_OHLCV_WINDOW_INCOMPLETE" in result["blockers"]


def test_interval_provenance_must_cover_entire_research_window(tmp_path: Path):
    _write_membership_sources(tmp_path)
    _write_raw_dataset(tmp_path, ["2024-10-08", "2024-10-09"])
    _write_interval_source(tmp_path, kind="status", effective_to="2024-10-08")
    _write_interval_source(tmp_path, kind="sector")
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["status_provenance_verified"] is True
    assert result["status_window_coverage_complete"] is False
    assert result["status_gap_day_count"] == 1
    assert result["execution_ready"] is False
    assert "HISTORICAL_STATUS_WINDOW_INCOMPLETE" in result["blockers"]


def test_syntactic_sha_without_source_artifact_is_not_verified_provenance(tmp_path: Path):
    _write_membership_sources(tmp_path)
    _write_raw_dataset(tmp_path, ["2024-10-08", "2024-10-09"])
    _write_interval_source(tmp_path, kind="status", include_artifact=False)
    _write_interval_source(tmp_path, kind="sector")
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["status_provenance_verified"] is False
    assert result["execution_ready"] is False
    assert "HISTORICAL_STATUS_PROVENANCE_INCOMPLETE" in result["blockers"]


def test_raw_file_without_manifest_binding_is_not_execution_ready(tmp_path: Path):
    _write_membership_sources(tmp_path)
    raw_dir = tmp_path / "data" / "backtest" / "raw_prices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "000584_SZ.csv").write_text("date,open,high,low,close,volume\n2024-10-08,1,1,1,1,1\n", encoding="utf-8")
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["raw_file_exists"] is True
    assert result["raw_manifest_hash_present"] is False
    assert result["raw_file_hash_match"] is False
    assert result["execution_ready"] is False


def test_committed_candidate_matrix_has_expected_readiness_split():
    root = Path(__file__).resolve().parents[1]
    audit = audit_missing_history_candidates(root)

    assert audit["candidate_count"] == 0
    assert audit["membership_ready_count"] == 0
    assert audit["execution_ready_count"] == 0
    assert audit["blocked_membership_count"] == 0
    assert len(audit["blocker_counts"]) == 0
