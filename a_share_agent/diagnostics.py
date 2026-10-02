from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .utils import now_shanghai, redact


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def write_diagnostic_report(
    project_root: str | Path,
    report_type: str,
    payload: dict[str, Any],
    *,
    ok: bool | None = None,
) -> dict[str, str]:
    """Persist a redacted deployment/runtime diagnostic report.

    Reports are intentionally separate from the immutable trading audit log.
    They may be overwritten via latest_<type>.json and are used for deployment
    troubleshooting, not as the source of truth for trading decisions.
    """
    root = Path(project_root).resolve()
    out_dir = root / "data" / "diagnostics"
    ts = now_shanghai()
    safe_type = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in report_type).strip("_") or "diagnostic"
    stamp = ts.strftime("%Y%m%d_%H%M%S_%f")
    report = redact({
        "report_type": safe_type,
        "generated_at": payload.get("generated_at") or ts.isoformat(),
        "ok": bool(ok) if ok is not None else None,
        "git_commit_sha": payload.get("git_commit_sha"),
        "git_branch": payload.get("git_branch"),
        "working_tree_clean": payload.get("working_tree_clean"),
        "producer_git_commit": payload.get("producer_git_commit") or payload.get("git_commit_sha"),
        "producer_code_version": payload.get("producer_code_version"),
        "research_start": payload.get("research_start"),
        "research_end": payload.get("research_end"),
        "preflight_artifact_current": payload.get("preflight_artifact_current"),
        "artifact_stale": payload.get("artifact_stale"),
        "payload": payload,
    })
    dated = out_dir / f"{safe_type}_{stamp}.json"
    latest = out_dir / f"latest_{safe_type}.json"
    _atomic_json_write(dated, report)
    _atomic_json_write(latest, report)
    if safe_type == "research_preflight":
        _atomic_json_write(root / "latest_research_preflight.json", report)
    return {"report": str(dated), "latest": str(latest)}
