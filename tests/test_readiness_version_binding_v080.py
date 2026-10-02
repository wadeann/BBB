import json
from pathlib import Path
from unittest.mock import patch

from a_share_agent.git_utils import check_artifact_stale, get_git_metadata
from a_share_agent.backtest.research import run_research_preflight
from a_share_agent.config import load_config


def test_git_metadata_retrieves_head_commit_and_code_version():
    root = Path(__file__).resolve().parents[1]
    meta = get_git_metadata(root)
    assert meta["git_commit_sha"] is not None
    assert len(meta["git_commit_sha"]) == 40
    assert meta["code_version"] == "0.7.7"
    assert meta["producer_code_version"] == "0.7.7"


def test_artifact_stale_detection_on_commit_mismatch():
    current_head = "977bf5a252282cf816bc6e696fe1b6f963e578fe"
    stale_artifact = {"producer_git_commit": "541fe89a1234567890abcdef1234567890abcdef"}
    fresh_artifact = {"producer_git_commit": current_head}
    missing_commit_artifact = {"generated_at": "2026-10-02T10:00:00Z"}

    assert check_artifact_stale(stale_artifact, current_head) is True
    assert check_artifact_stale(missing_commit_artifact, current_head) is True
    assert check_artifact_stale(fresh_artifact, current_head) is False


def test_preflight_blocks_readiness_when_artifact_stale(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root)

    # Mock get_git_metadata to return a different commit than the committed artifacts
    with patch("a_share_agent.backtest.research.get_git_metadata") as mock_git:
        mock_git.return_value = {
            "git_commit_sha": "0000000000000000000000000000000000000000",
            "git_branch": "master",
            "working_tree_clean": True,
            "producer_git_commit": "0000000000000000000000000000000000000000",
            "producer_code_version": "0.7.7",
            "code_version": "0.7.7",
            "generated_at": "2026-10-02T12:00:00Z",
        }
        res = run_research_preflight(cfg, mcp=None)
        assert res["artifact_stale"] is True
        assert len(res["stale_readiness_artifacts"]) > 0
        assert res["formal_full_market_ready"] is False
        assert any("STALE_READINESS_ARTIFACTS_DETECTED" in w for w in res["provider_warnings"])
