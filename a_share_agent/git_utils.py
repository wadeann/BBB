from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


def get_git_metadata(repo_root: Path | None = None) -> dict[str, Any]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]

    commit_sha: str | None = None
    branch: str | None = None
    is_clean = False

    try:
        res_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_commit.returncode == 0 and res_commit.stdout.strip():
            commit_sha = res_commit.stdout.strip()

        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_branch.returncode == 0 and res_branch.stdout.strip():
            branch = res_branch.stdout.strip()

        res_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_status.returncode == 0:
            is_clean = len(res_status.stdout.strip()) == 0
    except Exception:
        pass

    return {
        "git_commit_sha": commit_sha,
        "git_branch": branch,
        "working_tree_clean": is_clean,
        "producer_git_commit": commit_sha,
        "producer_code_version": __version__,
        "code_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def check_artifact_stale(
    artifact_data: dict[str, Any],
    current_commit: str | None,
    repo_root: Path | None = None,
) -> bool:
    if not current_commit:
        return False
    producer_commit = str(
        artifact_data.get("producer_git_commit")
        or artifact_data.get("git_commit_sha")
        or ""
    ).strip()
    if not producer_commit:
        return True
    if producer_commit == current_commit:
        return False
    if repo_root is not None:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD~1"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip() == producer_commit:
                return False
        except Exception:
            pass
    return True
