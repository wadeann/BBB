from pathlib import Path

from a_share_agent.backtest.data_integrity import sha256_file
from a_share_agent.backtest.pit_interval_coverage import audit_pit_interval_coverage


def _write_master(root: Path) -> Path:
    path = root / "security_master.csv"
    path.write_text(
        "symbol,board,listing_date,delisting_date,active_from,active_to\n"
        "600000.SH,SSE_MAIN,1999-11-10,,1999-11-10,\n",
        encoding="utf-8",
    )
    return path


def _write_provenance(root: Path, *, valid_artifact: bool = True) -> Path:
    artifact = root / "notice.txt"
    if valid_artifact:
        artifact.write_text("official notice", encoding="utf-8")
        digest = sha256_file(artifact)
    else:
        digest = "a" * 64
    path = root / "status_provenance.csv"
    path.write_text(
        "source_id,source_document_id_or_url,raw_source_hash,raw_source_path\n"
        f"SRC1,doc-1,{digest},{artifact.name}\n",
        encoding="utf-8",
    )
    return path


def test_pit_interval_coverage_requires_every_active_symbol_day(tmp_path: Path):
    master = _write_master(tmp_path)
    provenance = _write_provenance(tmp_path)
    intervals = tmp_path / "status.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,TRADABLE,2024-10-08,2024-10-09,SRC1\n",
        encoding="utf-8",
    )
    audit = audit_pit_interval_coverage(
        master,
        intervals,
        provenance,
        ["2024-10-08", "2024-10-09"],
        value_fields=("status",),
        min_provenance_coverage=1.0,
    )
    assert audit["expected_symbol_days"] == 2
    assert audit["covered_symbol_days"] == 2
    assert audit["missing_symbol_days"] == 0
    assert audit["min_daily_pit_coverage"] == 1.0
    assert audit["dataset_complete"] is True


def test_pit_interval_coverage_rejects_temporal_gap(tmp_path: Path):
    master = _write_master(tmp_path)
    provenance = _write_provenance(tmp_path)
    intervals = tmp_path / "status.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,TRADABLE,2024-10-08,2024-10-08,SRC1\n",
        encoding="utf-8",
    )
    audit = audit_pit_interval_coverage(
        master,
        intervals,
        provenance,
        ["2024-10-08", "2024-10-09"],
        value_fields=("status",),
        min_provenance_coverage=1.0,
    )
    assert audit["missing_symbol_days"] == 1
    assert audit["temporal_coverage"] == 0.5
    assert audit["dataset_complete"] is False


def test_pit_interval_coverage_rejects_overlapping_or_conflicting_intervals(tmp_path: Path):
    master = _write_master(tmp_path)
    provenance = _write_provenance(tmp_path)
    intervals = tmp_path / "status.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,TRADABLE,2024-10-08,2024-10-09,SRC1\n"
        "600000.SH,SUSPENDED,2024-10-09,2024-10-09,SRC1\n",
        encoding="utf-8",
    )
    audit = audit_pit_interval_coverage(
        master,
        intervals,
        provenance,
        ["2024-10-08", "2024-10-09"],
        value_fields=("status",),
        min_provenance_coverage=1.0,
    )
    assert audit["overlap_symbol_days"] == 1
    assert audit["conflict_symbol_days"] == 1
    assert audit["dataset_complete"] is False


def test_pit_interval_coverage_rejects_unverified_source_artifact(tmp_path: Path):
    master = _write_master(tmp_path)
    provenance = _write_provenance(tmp_path, valid_artifact=False)
    intervals = tmp_path / "status.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,TRADABLE,2024-10-08,2024-10-09,SRC1\n",
        encoding="utf-8",
    )
    audit = audit_pit_interval_coverage(
        master,
        intervals,
        provenance,
        ["2024-10-08", "2024-10-09"],
        value_fields=("status",),
        min_provenance_coverage=1.0,
    )
    assert audit["unverified_symbol_days"] == 2
    assert audit["source_provenance_audit"]["missing_source_artifacts"] == 1
    assert audit["dataset_complete"] is False
