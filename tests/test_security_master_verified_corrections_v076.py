import importlib.util
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "apply_security_master_verified_corrections.py"
    spec = importlib.util.spec_from_file_location("apply_security_master_verified_corrections", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_verified_correction_only_clears_false_early_delisting(tmp_path: Path):
    module = _load_script()
    master = tmp_path / "security_master.csv"
    corrections = tmp_path / "corrections.csv"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    audit = tmp_path / "audit.csv"

    master.write_text(
        "symbol,name,active_from,active_to,board,listing_date,delisting_date\n"
        "600293.SH,x,2000-09-19,2026-09-28,SSE_MAIN,2000-09-19,2026-09-28\n"
        "600000.SH,y,1999-11-10,,SSE_MAIN,1999-11-10,\n",
        encoding="utf-8",
    )
    corrections.write_text(
        "symbol,action,verified_active_through,evidence_snapshot_date,evidence_source,evidence_document_id_or_url,reason\n"
        "600293.SH,CLEAR_FALSE_EARLY_DELISTING,2026-09-30,2026-09-30,SSE_OFFICIAL_LISTING_REGISTER,doc,still listed\n",
        encoding="utf-8",
    )
    (snapshots / "2026-09-30.csv").write_text(
        "date,symbol,exchange,board,security_type,listing_date\n"
        "2026-09-30,600293.SH,SSE,SSE_MAIN,A_SHARE_COMMON_EQUITY,2000-09-19\n",
        encoding="utf-8",
    )

    dry = module.apply_corrections(master, corrections, snapshots, apply=False, audit_path=audit)
    assert dry["changed_rows"] == 1
    assert "2026-09-28" in master.read_text(encoding="utf-8")

    applied = module.apply_corrections(master, corrections, snapshots, apply=True, audit_path=audit)
    assert applied["changed_rows"] == 1
    text = master.read_text(encoding="utf-8-sig")
    assert "2026-09-28" not in text
    assert "600293.SH" in text


def test_verified_correction_rejects_symbol_absent_from_evidence_snapshot(tmp_path: Path):
    module = _load_script()
    master = tmp_path / "security_master.csv"
    corrections = tmp_path / "corrections.csv"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    audit = tmp_path / "audit.csv"

    master.write_text(
        "symbol,name,active_from,active_to,board,listing_date,delisting_date\n"
        "600293.SH,x,2000-09-19,2026-09-28,SSE_MAIN,2000-09-19,2026-09-28\n",
        encoding="utf-8",
    )
    corrections.write_text(
        "symbol,action,verified_active_through,evidence_snapshot_date,evidence_source,evidence_document_id_or_url,reason\n"
        "600293.SH,CLEAR_FALSE_EARLY_DELISTING,2026-09-30,2026-09-30,SSE_OFFICIAL_LISTING_REGISTER,doc,still listed\n",
        encoding="utf-8",
    )
    (snapshots / "2026-09-30.csv").write_text(
        "date,symbol,exchange,board,security_type,listing_date\n"
        "2026-09-30,600000.SH,SSE,SSE_MAIN,A_SHARE_COMMON_EQUITY,1999-11-10\n",
        encoding="utf-8",
    )

    try:
        module.apply_corrections(master, corrections, snapshots, apply=True, audit_path=audit)
    except RuntimeError as exc:
        assert "does not contain 600293.SH" in str(exc)
    else:
        raise AssertionError("correction must fail closed without snapshot evidence")
