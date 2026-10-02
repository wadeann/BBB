import csv
import json
import subprocess
from pathlib import Path

from a_share_agent.backtest.data_integrity import sha256_file
from a_share_agent.backtest.raw_price_provenance import audit_raw_price_provenance
from a_share_agent.git_utils import check_artifact_stale, get_git_metadata


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")


def test_commit_bound_source_rejects_dirty_code_but_allows_untracked_evidence(tmp_path: Path):
    _init_repo(tmp_path)
    tracked = tmp_path / "pyproject.toml"
    tracked.write_text("[project]\nname='x'\nversion='0.0.1'\n", encoding="utf-8")
    _git(tmp_path, "add", "pyproject.toml")
    _git(tmp_path, "commit", "-m", "baseline")

    clean = get_git_metadata(tmp_path)
    assert clean["commit_bound_execution_ready"] is True
    assert clean["tracked_tree_clean"] is True
    assert clean["working_tree_clean"] is True

    evidence = tmp_path / "data" / "backtest" / "evidence" / "official.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("independent official evidence", encoding="utf-8")
    evidence_only = get_git_metadata(tmp_path)
    assert evidence_only["working_tree_clean"] is False
    assert evidence_only["commit_bound_execution_ready"] is True

    source = tmp_path / "a_share_agent" / "new_gate.py"
    source.parent.mkdir(parents=True)
    source.write_text("FLAG = True\n", encoding="utf-8")
    untracked_source = get_git_metadata(tmp_path)
    assert untracked_source["commit_bound_execution_ready"] is False
    assert "a_share_agent/new_gate.py" in untracked_source["untracked_source_files"]

    source.unlink()
    tracked.write_text("[project]\nname='x'\nversion='0.0.2'\n", encoding="utf-8")
    dirty_tracked = get_git_metadata(tmp_path)
    assert dirty_tracked["tracked_tree_clean"] is False
    assert dirty_tracked["commit_bound_execution_ready"] is False
    assert dirty_tracked["source_provenance_reason"] == "TRACKED_WORKTREE_CHANGES_PRESENT"


def test_artifact_parent_is_fresh_only_across_readiness_output_commits(tmp_path: Path):
    _init_repo(tmp_path)
    source = tmp_path / "a_share_agent" / "code.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    report = tmp_path / "latest_research_preflight.json"
    report.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "source baseline")
    producer = _git(tmp_path, "rev-parse", "HEAD")

    report.write_text('{"refreshed": true}\n', encoding="utf-8")
    _git(tmp_path, "add", "latest_research_preflight.json")
    _git(tmp_path, "commit", "-m", "refresh report")
    report_only_head = _git(tmp_path, "rev-parse", "HEAD")
    assert check_artifact_stale(
        {"producer_git_commit": producer},
        report_only_head,
        repo_root=tmp_path,
    ) is False

    source.write_text("VALUE = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "a_share_agent/code.py")
    _git(tmp_path, "commit", "-m", "change code")
    code_head = _git(tmp_path, "rev-parse", "HEAD")
    assert check_artifact_stale(
        {"producer_git_commit": report_only_head},
        code_head,
        repo_root=tmp_path,
    ) is True


def test_raw_price_provenance_requires_bound_physical_source_artifact(tmp_path: Path):
    normalized_name = "600000_SH.csv"
    normalized_hash = "1" * 64
    raw_manifest = tmp_path / "raw_dataset_manifest.json"
    raw_manifest.write_text(
        json.dumps(
            {
                "summary": {"overall_dataset_hash": "a" * 64},
                "file_hashes": {normalized_name: normalized_hash},
            }
        ),
        encoding="utf-8",
    )

    backtest = tmp_path / "data" / "backtest"
    evidence = backtest / "evidence" / "market-export.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("official market export row for 600000.SH", encoding="utf-8")

    provenance = backtest / "raw_price_provenance.csv"
    with provenance.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "raw_filename",
                "normalized_sha256",
                "source_id",
                "source_document_id_or_url",
                "source_record_id",
                "source_artifact_path",
                "source_artifact_sha256",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "raw_filename": normalized_name,
                "normalized_sha256": normalized_hash,
                "source_id": "official-export",
                "source_document_id_or_url": "official-doc-1",
                "source_record_id": "600000.SH",
                "source_artifact_path": "data/backtest/evidence/market-export.txt",
                "source_artifact_sha256": sha256_file(evidence),
            }
        )

    manifest = backtest / "raw_price_provenance_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_type": "INDEPENDENT_MARKET_DATA_EXPORT",
                "source_dataset_id": "market-export-test",
                "normalized_dataset_hash": "a" * 64,
                "raw_dataset_manifest_sha256": sha256_file(raw_manifest),
                "provenance_csv_sha256": sha256_file(provenance),
                "provenance_row_count": 1,
                "date_range": {"start": "2024-10-01", "end": "2026-09-30"},
                "normalization": {"mode": "DIRECT_SOURCE_EXPORT"},
            }
        ),
        encoding="utf-8",
    )

    ok = audit_raw_price_provenance(
        tmp_path,
        actual_dataset_hash="a" * 64,
        research_start="2024-10-01",
        research_end="2026-09-30",
    )
    assert ok["verified"] is True

    evidence.write_text("tampered source bytes", encoding="utf-8")
    tampered = audit_raw_price_provenance(
        tmp_path,
        actual_dataset_hash="a" * 64,
        research_start="2024-10-01",
        research_end="2026-09-30",
    )
    assert tampered["verified"] is False
    assert tampered["source_artifact_hash_mismatches"] == 1


def test_research_preflight_wires_raw_and_source_provenance_gates():
    import a_share_agent.backtest.research as research

    source = Path(research.__file__).read_text(encoding="utf-8")
    assert '"30_raw_price_provenance_verified": raw_provenance_verified' in source
    assert 'checklist["31_commit_bound_clean_source"] = commit_bound_source' in source
    assert "and raw_provenance_verified" in source
    assert 'warnings.append("RAW_PRICE_PROVENANCE_UNAVAILABLE_OR_UNVERIFIED")' in source
    assert 'warnings.append("SOURCE_TREE_NOT_CLEAN_OR_COMMIT_BOUND")' in source
