import csv
import importlib.util
import json
from pathlib import Path


def _load_script(root: Path):
    script = root / "scripts" / "build_missing_history_candidates.py"
    spec = importlib.util.spec_from_file_location("build_missing_history_candidates_v077", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_matrix_matches_current_unique_universe_gaps():
    root = Path(__file__).resolve().parents[1]
    module = _load_script(root)
    summary = module.build_candidates()
    audit = json.loads((root / "historical_data_provenance_audit.json").read_text(encoding="utf-8"))
    unique_missing = audit["universe"]["unique_missing_symbols"]

    assert summary["candidate_count"] == len(unique_missing)
    assert summary["master_insert_approved_count"] == 0
    assert summary["unresolved_source_record_count"] == 0

    with (root / "security_master_missing_history_candidates.csv").open("r", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert {row["symbol"] for row in rows} == set(unique_missing)
    assert all(row["master_insert_approved"] == "False" for row in rows)

    szse = [row for row in rows if row["exchange"] == "SZSE"]
    sse = [row for row in rows if row["exchange"] == "SSE"]
    assert len(szse) == 17
    assert len(sse) == 18
    assert all(row["candidate_status"] == "DATE_PROVENANCE_READY_MASTER_INSERT_NOT_YET_APPROVED" for row in szse)
    assert all(row["raw_delisting_field"] == "终止上市日期" for row in szse)
    assert all(row["candidate_status"] == "BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS" for row in sse)
    assert all(row["raw_delisting_field"] == "暂停上市日期" for row in sse)
