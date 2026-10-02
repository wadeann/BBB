from pathlib import Path
import json

from a_share_agent.backtest.data_integrity import verify_coverage_binding, verify_raw_dataset_manifest
from a_share_agent.backtest.research import _is_a_share_common_equity_symbol, _corporate_action_audit


def test_asset_type_guard_excludes_b_shares_and_cdr():
    assert _is_a_share_common_equity_symbol("600000.SH", "SSE_MAIN")
    assert _is_a_share_common_equity_symbol("688981.SH", "STAR")
    assert not _is_a_share_common_equity_symbol("689009.SH", "STAR")
    assert not _is_a_share_common_equity_symbol("900957.SH", "SSE_MAIN")
    assert not _is_a_share_common_equity_symbol("200413.SZ", "SZSE_MAIN")


def test_ca_fails_closed_without_independent_register(tmp_path: Path):
    d = tmp_path / "data" / "backtest"; d.mkdir(parents=True)
    (d / "corporate_actions.csv").write_text("symbol,action_type,ex_date,record_date,source_url_or_document_id,verified\n600000.SH,cash_dividend,2026-01-02,2026-01-01,doc,True\n")
    audit = _corporate_action_audit(tmp_path)
    assert audit["official_register_valid"] is False
    assert audit["complete"] is False


def test_raw_manifest_and_coverage_binding_reject_stale_artifacts(tmp_path: Path):
    raw = tmp_path / "raw"; raw.mkdir()
    (raw / "600000_SH.csv").write_text("date,open,high,low,close,volume\n2026-01-02,1,1,1,1,1\n")
    manifest = tmp_path / "raw_dataset_manifest.json"
    manifest.write_text(json.dumps({"summary":{"file_count":1,"row_count":1,"overall_dataset_hash":"bad"},"file_hashes":{"600000_SH.csv":"bad"}}))
    integ = verify_raw_dataset_manifest(raw, manifest)
    assert integ["raw_dataset_hash_match"] is False
    cov = tmp_path / "daily_raw_coverage.csv"; cov.write_text("date,raw_coverage_pct\n2026-01-02,100%\n")
    cov_manifest = tmp_path / "daily_raw_coverage_manifest.json"
    cov_manifest.write_text(json.dumps({"source_raw_dataset_hash":"other","coverage_csv_sha256":"bad"}))
    bind = verify_coverage_binding(cov, cov_manifest, integ.get("actual_raw_dataset_hash"))
    assert bind["daily_raw_coverage_fresh"] is False
