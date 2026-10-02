from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from a_share_agent.backtest.provenance_audit import (
    audit_interval_provenance,
    classify_universe_difference,
    reconcile_corporate_action_sets,
    validate_official_ca_register,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_interval_provenance_requires_explicit_sidecar(tmp_path: Path):
    intervals = tmp_path / "historical_status_intervals.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,TRADABLE,2024-01-01,,SSE_OFFICIAL_STATUS_REGISTER\n",
        encoding="utf-8",
    )
    audit = audit_interval_provenance(intervals, tmp_path / "status_provenance.csv")
    assert audit["provenance_sidecar_present"] is False
    assert audit["source_coverage"] == 0.0
    assert audit["dataset_complete"] is False


def test_interval_provenance_accepts_hashed_source_sidecar(tmp_path: Path):
    intervals = tmp_path / "historical_status_intervals.csv"
    intervals.write_text(
        "symbol,status,effective_from,effective_to,source\n"
        "600000.SH,SUSPENDED,2024-01-01,2024-01-02,SSE_NOTICE_1\n",
        encoding="utf-8",
    )
    source_file = tmp_path / "notice.pdf.txt"
    source_file.write_text("official notice", encoding="utf-8")
    sidecar = tmp_path / "status_provenance.csv"
    sidecar.write_text(
        "source_id,source_document_id_or_url,raw_source_hash\n"
        f"SSE_NOTICE_1,https://example.invalid/notice,{_sha(source_file)}\n",
        encoding="utf-8",
    )
    audit = audit_interval_provenance(intervals, sidecar, min_coverage=1.0)
    assert audit["source_coverage"] == 1.0
    assert audit["dataset_complete"] is True


def test_official_ca_register_requires_row_level_source_hash(tmp_path: Path):
    register = tmp_path / "official.csv"
    register.write_text(
        "symbol,action_type,ex_date,record_date,source,source_document_id_or_url,raw_source_hash\n"
        "600000.SH,CASH_DIVIDEND,2025-06-01,2025-05-31,SSE,doc,\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_type": "INDEPENDENT_OFFICIAL_EXPORT",
                "source_dataset_id": "official-test",
                "register_sha256": _sha(register),
                "event_count": 1,
                "source_files": [{"source_id": "raw-1", "sha256": "0" * 64}],
            }
        ),
        encoding="utf-8",
    )
    status = validate_official_ca_register(register, manifest)
    assert status["valid"] is False
    assert status["rows_missing_provenance"] == 1


def test_corporate_action_reconciliation_uses_independent_source_hashes(tmp_path: Path):
    raw = tmp_path / "source.txt"
    raw.write_text("exchange export row", encoding="utf-8")
    raw_hash = _sha(raw)
    register = tmp_path / "official.csv"
    header = "symbol,action_type,ex_date,record_date,cash_dividend_per_share,source,source_document_id_or_url,raw_source_hash\n"
    row = f"600000.SH,CASH_DIVIDEND,2025-06-01,2025-05-31,0.1,SSE,doc-1,{raw_hash}\n"
    register.write_text(header + row, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_type": "INDEPENDENT_OFFICIAL_EXPORT",
                "source_dataset_id": "official-test",
                "register_sha256": _sha(register),
                "event_count": 1,
                "source_files": [{"source_id": "raw-1", "sha256": raw_hash}],
            }
        ),
        encoding="utf-8",
    )
    prod = tmp_path / "prod.csv"
    prod.write_text(header + row, encoding="utf-8")
    result = reconcile_corporate_action_sets(prod, register, manifest)
    assert result["official_register_valid"] is True
    assert result["matched_events"] == 1
    assert result["complete"] is True


def test_universe_difference_classifier_is_fail_closed():
    assert classify_universe_difference(
        difference_type="MISSING_IN_LOCAL",
        audit_date="2026-07-01",
        official_row={"symbol": "600000.SH"},
        local_rows=[],
    ) == "LOCAL_MASTER_MISSING"
    assert classify_universe_difference(
        difference_type="EXTRA_IN_LOCAL",
        audit_date="2026-07-01",
        official_row=None,
        local_rows=[{"listing_date": "2026-07-10", "active_from": "2026-07-10"}],
    ) == "LOCAL_PRELISTING_LEAKAGE"
