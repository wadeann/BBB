from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


_SOURCE_PREFIXES = (
    "a_share_agent/",
    "scripts/",
    "tests/",
    ".github/",
    "config/",
)
_SOURCE_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
}
_READINESS_OUTPUT_PATHS = {
    "HISTORICAL_DATA_BUILD_PROGRESS.md",
    "MISSING_HISTORY_READINESS_REPORT.md",
    "daily_raw_coverage.csv",
    "daily_raw_coverage_manifest.json",
    "historical_data_provenance_audit.json",
    "latest_research_preflight.json",
    "raw_dataset_manifest.json",
    "security_master_missing_history_candidates_summary.json",
    "security_master_missing_history_readiness.csv",
    "security_master_missing_history_readiness_summary.json",
}
_READINESS_OUTPUT_PREFIXES = ("data/diagnostics/",)


def _run_git(repo_root: Path, *args: str, timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None


def _normalize_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_source_path(path: str) -> bool:
    normalized = _normalize_path(path)
    return normalized in _SOURCE_FILES or normalized.startswith(_SOURCE_PREFIXES)


def _is_readiness_output_path(path: str) -> bool:
    normalized = _normalize_path(path)
    return normalized in _READINESS_OUTPUT_PATHS or normalized.startswith(_READINESS_OUTPUT_PREFIXES)


def get_git_metadata(repo_root: Path | None = None) -> dict[str, Any]:
    """Return Git producer metadata and a fail-closed commit-bound source verdict.

    A formal research run may have untracked mounted data/evidence because those bytes
    are authenticated by their own provenance manifests. It must not run from modified
    tracked files or from untracked source/config/test/workflow files: in those cases a
    HEAD SHA alone does not uniquely identify the producer code.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]
    repo_root = Path(repo_root).resolve()

    commit_sha: str | None = None
    branch: str | None = None
    working_tree_clean = False
    tracked_tree_clean = False
    tracked_changes: list[str] = []
    untracked_source_files: list[str] = []
    git_available = False

    top = _run_git(repo_root, "rev-parse", "--show-toplevel")
    if top is not None and top.returncode == 0 and top.stdout.strip():
        git_available = True
        repo_root = Path(top.stdout.strip()).resolve()

        res_commit = _run_git(repo_root, "rev-parse", "HEAD")
        if res_commit is not None and res_commit.returncode == 0 and res_commit.stdout.strip():
            commit_sha = res_commit.stdout.strip()

        res_branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        if res_branch is not None and res_branch.returncode == 0 and res_branch.stdout.strip():
            branch = res_branch.stdout.strip()

        res_status = _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
        if res_status is not None and res_status.returncode == 0:
            lines = [line for line in res_status.stdout.splitlines() if line]
            working_tree_clean = not lines
            for line in lines:
                code = line[:2]
                path = line[3:].strip() if len(line) > 3 else ""
                if code == "??":
                    if _is_source_path(path):
                        untracked_source_files.append(path)
                else:
                    tracked_changes.append(line)
            tracked_tree_clean = not tracked_changes

    commit_bound_execution_ready = bool(
        git_available
        and commit_sha
        and tracked_tree_clean
        and not untracked_source_files
    )
    if not git_available:
        source_reason = "GIT_REPOSITORY_UNAVAILABLE"
    elif not commit_sha:
        source_reason = "HEAD_COMMIT_UNAVAILABLE"
    elif tracked_changes:
        source_reason = "TRACKED_WORKTREE_CHANGES_PRESENT"
    elif untracked_source_files:
        source_reason = "UNTRACKED_SOURCE_FILES_PRESENT"
    else:
        source_reason = "OK"

    return {
        "git_commit_sha": commit_sha,
        "git_branch": branch,
        "working_tree_clean": working_tree_clean,
        "tracked_tree_clean": tracked_tree_clean,
        "tracked_changes": tracked_changes,
        "untracked_source_files": sorted(untracked_source_files),
        "commit_bound_execution_ready": commit_bound_execution_ready,
        "source_provenance_reason": source_reason,
        "git_available": git_available,
        "git_root": str(repo_root) if git_available else None,
        "producer_git_commit": commit_sha,
        "producer_code_version": __version__,
        "code_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _only_readiness_outputs_changed(
    repo_root: Path,
    producer_commit: str,
    current_commit: str,
) -> bool:
    ancestor = _run_git(repo_root, "merge-base", "--is-ancestor", producer_commit, current_commit)
    if ancestor is None or ancestor.returncode != 0:
        return False
    diff = _run_git(repo_root, "diff", "--name-only", f"{producer_commit}..{current_commit}", "--")
    if diff is None or diff.returncode != 0:
        return False
    changed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    return bool(changed) and all(_is_readiness_output_path(path) for path in changed)


def check_artifact_stale(
    artifact_data: dict[str, Any],
    current_commit: str | None,
    repo_root: Path | None = None,
) -> bool:
    """Return True unless an artifact is bound to the current research inputs.

    An artifact produced at an earlier commit remains current only when every tracked
    change since that producer is an explicitly allow-listed generated readiness
    output. This permits report-only follow-up commits without treating a code/data
    commit as harmless merely because the producer happens to be its parent.
    """
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
    if repo_root is None:
        return True
    return not _only_readiness_outputs_changed(
        Path(repo_root).resolve(),
        producer_commit,
        current_commit,
    )
