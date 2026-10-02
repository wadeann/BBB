import csv
import hashlib
import json
from pathlib import Path

from a_share_agent.backtest.missing_history_readiness import assess_missing_history_candidate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        "raw_record_found": "True",
        "source_allows_delisting_date": "True",
        "candidate_status": "DATE_PROVENANCE_READY_MASTER_INSERT_NOT_YET_APPROVED",
    }


def test_membership_ready_does_not_imply_execution_ready(tmp_path: Path):
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["membership_ready"] is True
    assert result["execution_ready"] is False
    assert "RAW_OHLCV_NOT_MANIFEST_VERIFIED" in result["blockers"]
    assert "HISTORICAL_STATUS_PROVENANCE_INCOMPLETE" in result["blockers"]
    assert "HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE" in result["blockers"]


def test_ambiguous_delisting_semantics_blocks_membership(tmp_path: Path):
    candidate = _candidate()
    candidate["source_allows_delisting_date"] = "False"
    candidate["candidate_status"] = "BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS"
    result = assess_missing_history_candidate(tmp_path, candidate)
    assert result["membership_ready"] is False
    assert result["execution_ready"] is False
    assert "PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE" in result["blockers"]


def test_execution_ready_requires_hash_bound_raw_and_interval_provenance(tmp_path: Path):
    symbol = "000584.SZ"
    raw_dir = tmp_path / "data" / "backtest" / "raw_prices"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "000584_SZ.csv"
    raw_file.write_text("date,open,high,low,close,volume\n2025-01-02,1,1,1,1,1\n", encoding="utf-8")
    raw_hash = _sha(raw_file)
    (tmp_path / "raw_dataset_manifest.json").write_text(
        json.dumps({"file_hashes": {raw_file.name: raw_hash}}),
        encoding="utf-8",
    )

    status_source_hash = "1" * 64
    sector_source_hash = "2" * 64
    _write_csv(
        tmp_path / "data" / "backtest" / "historical_status_intervals.csv",
        ["symbol", "status", "effective_from", "effective_to", "source"],
        [{"symbol": symbol, "status": "TRADABLE", "effective_from": "2024-01-01", "effective_to": "2025-07-11", "source": "STATUS_DOC_1"}],
    )
    _write_csv(
        tmp_path / "data" / "backtest" / "status_provenance.csv",
        ["source_id", "source_document_id_or_url", "raw_source_hash"],
        [{"source_id": "STATUS_DOC_1", "source_document_id_or_url": "doc-status", "raw_source_hash": status_source_hash}],
    )
    _write_csv(
        tmp_path / "data" / "backtest" / "historical_sector_intervals.csv",
        ["symbol", "sector_code", "effective_from", "effective_to", "source"],
        [{"symbol": symbol, "sector_code": "S1", "effective_from": "2024-01-01", "effective_to": "2025-07-11", "source": "SECTOR_DOC_1"}],
    )
    _write_csv(
        tmp_path / "data" / "backtest" / "sector_provenance.csv",
        ["source_id", "source_document_id_or_url", "raw_source_hash"],
        [{"source_id": "SECTOR_DOC_1", "source_document_id_or_url": "doc-sector", "raw_source_hash": sector_source_hash}],
    )

    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["membership_ready"] is True
    assert result["raw_file_hash_match"] is True
    assert result["status_provenance_verified"] is True
    assert result["sector_provenance_verified"] is True
    assert result["execution_ready"] is True
    assert result["blockers"] == []


def test_raw_file_without_manifest_binding_is_not_execution_ready(tmp_path: Path):
    raw_dir = tmp_path / "data" / "backtest" / "raw_prices"
    raw_dir.mkdir(parents=True)
    (raw_dir / "000584_SZ.csv").write_text("date,open,high,low,close,volume\n2025-01-02,1,1,1,1,1\n", encoding="utf-8")
    result = assess_missing_history_candidate(tmp_path, _candidate())
    assert result["raw_file_exists"] is True
    assert result["raw_manifest_hash_present"] is False
    assert result["raw_file_hash_match"] is False
    assert result["execution_ready"] is False
