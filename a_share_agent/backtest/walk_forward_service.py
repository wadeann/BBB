"""Shared walk-forward stability runner — Phase 2A.5.

Orchestrates the end-to-end walk-forward stability pipeline:

    1. Load WalkForwardConfig and generate folds
    2. Preflight each fold (calendar, data, universe coverage)
    3. Execute each fold via BacktestEngine with evaluation_window
    4. Summarize fold results → OOS stability aggregate
    5. Persist manifest, fold artifacts, OOS reports, methodology

Owns no modules — imports peer interfaces from their respective owners.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..git_utils import get_git_metadata
from .. import __version__

# ---------------------------------------------------------------------------
# Peer interface imports — resolve at final merge
# ---------------------------------------------------------------------------
try:
    from ..backtest.walk_forward import WalkForwardConfig, generate_folds
    _WF_AVAILABLE = True
except ImportError:
    WalkForwardConfig = None  # type: ignore[assignment]
    WalkForwardEngine = None  # type: ignore[assignment]
    _WF_AVAILABLE = False

try:
    from ..backtest.walk_forward_preflight import preflight_fold
    _PREFLIGHT_AVAILABLE = True
except ImportError:
    preflight_fold = None  # type: ignore[assignment]
    _PREFLIGHT_AVAILABLE = False

try:
    from ..backtest.oos_stability import summarize_fold, aggregate_stability
    _OOS_AVAILABLE = True
except ImportError:
    summarize_fold = None  # type: ignore[assignment]
    aggregate_stability = None  # type: ignore[assignment]
    _OOS_AVAILABLE = False

try:
    from ..backtest.engine import BacktestEngine
    from ..backtest.data import HistoricalDataProvider
    from ..backtest.models import BacktestSettings
    from ..backtest.service import settings_from
    _ENGINE_AVAILABLE = True
except ImportError:
    BacktestEngine = None  # type: ignore[assignment]
    HistoricalDataProvider = None  # type: ignore[assignment]
    BacktestSettings = None  # type: ignore[assignment]
    settings_from = None  # type: ignore[assignment]
    _ENGINE_AVAILABLE = False

from ..backtest.walk_forward_persistence import (
    write_finite_json as _write_finite_json,
    write_csv as _write_csv,
    write_fold_artifacts,
    write_oos_stability,
)


# ---------------------------------------------------------------------------
# Config rule keys for rule hashing
# ---------------------------------------------------------------------------
_CONFIG_RULE_KEYS = (
    "router", "scanner", "regime", "context", "scoring", "cost_source",
)


def _compute_rule_hashes(runtime_cfg: RuntimeConfig) -> dict[str, str]:
    """Compute SHA-256 hashes for relevant config rule sections."""
    hashes: dict[str, str] = {}
    for key in _CONFIG_RULE_KEYS:
        section = getattr(runtime_cfg, key, None) or {}
        raw = json.dumps(section, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        hashes[f"{key}_sha256"] = h
    return hashes


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_entry(path: Path, *tags: str) -> dict[str, Any]:
    """Build a file input entry with SHA-256."""
    try:
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        sha256 = None
    return {
        "path": str(path.relative_to(path.anchor)),
        "sha256": sha256,
        "size_bytes": path.stat().st_size if path.exists() else None,
        "tags": sorted(set(tags)),
    }


def _scan_physical_inputs(project_root: Path) -> list[dict[str, Any]]:
    """List all consumed physical input files with their properties."""
    inputs: list[dict[str, Any]] = []
    data_dir = project_root / "data"

    # Calendar sources
    cal_path = data_dir / "backtest" / "trading_grade_calendar.csv"
    if cal_path.is_file():
        inputs.append(_file_entry(cal_path, "calendar"))

    # tier 2: all backtest data files
    for pattern in ("backtest/**/*.csv", "backtest/**/*.json", "backtest/**/*.parquet"):
        for p in sorted(data_dir.glob(pattern)):
            if p.is_file() and p != cal_path:
                tags = ["data"]
                name_lower = p.name.lower()
                if "sector" in name_lower:
                    tags.append("sector")
                if "status" in name_lower or "tradable" in name_lower:
                    tags.append("status")
                if "universe" in name_lower:
                    tags.append("universe")
                if "corporate" in name_lower or "action" in name_lower:
                    tags.append("corporate_action")
                if "benchmark" in name_lower:
                    tags.append("benchmark")
                if "calendar" in name_lower:
                    tags.append("calendar")
                inputs.append(_file_entry(p, *tags))

    # Config files
    for p in sorted((project_root / "config").rglob("*.yaml")):
        inputs.append(_file_entry(p, "config"))

    # Key source files
    for rel in (
        "a_share_agent/backtest/walk_forward.py",
        "a_share_agent/backtest/engine.py",
        "a_share_agent/backtest/data.py",
        "a_share_agent/backtest/models.py",
        "a_share_agent/backtest/metrics.py",
        "a_share_agent/backtest/report.py",
        "a_share_agent/config/backtest.yaml",
    ):
        p = project_root / rel
        if p.is_file():
            inputs.append(_file_entry(p, "source"))

    return inputs


def _assert_finite_json(raw: str) -> None:
    """Assert that a JSON string contains no NaN or Infinity tokens."""
    for tok in ("NaN", "Infinity", "-Infinity"):
        if tok in raw:
            raise ValueError(f"Non-finite JSON value detected: {tok}")


def _write_finite_json(path: Path, data: Any) -> None:
    """Write JSON strictly without NaN/Infinity."""
    raw = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    _assert_finite_json(raw)
    path.write_text(raw, encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to CSV, handling nested types."""
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in row.items()})


def _safe_run_id(user_run_id: str | None) -> str:
    """Generate a safe run ID from user input or auto-generate."""
    if user_run_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_run_id)
        if not safe:
            safe = f"wf-{uuid.uuid4().hex[:12]}"
        return safe
    return f"wf-{uuid.uuid4().hex[:12]}"


def _list_non_empty(directory: Path) -> list[Path]:
    """List files in a directory to check if it's non-empty."""
    if not directory.is_dir():
        return []
    return [p for p in directory.iterdir()
            if p.name != "__pycache__" and not p.name.startswith(".tmp")]


def _is_incomplete_run(directory: Path) -> bool:
    """Check if a directory represents an incomplete run (no manifest)."""
    return not (directory / "manifest.json").exists()


def _has_matching_run_id(directory: Path, run_id: str) -> bool:
    """Check if a directory has a manifest with the given run_id."""
    manifest = directory / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("run_id") == run_id
        except Exception:
            return False
    return False


def _write_stability_csv(path: Path, stability: dict[str, Any],
                         summaries: list[dict[str, Any]]) -> None:
    """Write per-fold and aggregate stability rows as CSV."""
    rows: list[dict[str, Any]] = []
    for s in summaries:
        rows.append({
            "fold_id": s.get("fold_id"),
            "n_closed": s.get("n_closed", 0),
            "n_open": s.get("n_open", 0),
            "n_censored": s.get("n_censored", 0),
            "win_rate": s.get("win_rate"),
            "expectancy_pct": s.get("expectancy_pct"),
            "median_trade_pct": s.get("median_trade_pct"),
            "profit_factor": s.get("profit_factor"),
            "pf_reason": s.get("pf_reason"),
            "max_drawdown_pct": s.get("max_drawdown_pct"),
            "avg_mfe_pct": s.get("avg_mfe_pct"),
            "avg_mae_pct": s.get("avg_mae_pct"),
        })
    rows.append({
        "fold_id": "__aggregate__",
        "n_closed": stability.get("n_total_closed"),
        "classification": stability.get("classification"),
        "n_observed_folds": stability.get("n_observed_folds"),
        "n_informative_folds": stability.get("n_informative_folds"),
        "n_failed_folds": stability.get("n_failed_folds"),
        "pooled_median_trade_pct": stability.get("pooled_median_trade_pct"),
        "fold_median_trade_pct": stability.get("fold_median_trade_pct"),
        "fold_median_expectancy_pct": stability.get("fold_median_expectancy_pct"),
        "fold_median_profit_factor": stability.get("fold_median_profit_factor"),
        "max_fold_return_concentration": stability.get("max_fold_return_concentration"),
    })
    _write_csv(path, rows)


def _resolve_dependencies() -> dict[str, str]:
    """Resolve Python package versions relevant to the pipeline."""
    deps: dict[str, str] = {}
    for mod_name in ("numpy", "pandas", "pyyaml", "scipy"):
        try:
            mod = __import__(mod_name)
            deps[mod_name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            deps[mod_name] = "not_installed"
    return deps


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def run_stability(
    runtime_cfg: RuntimeConfig,
    wf_cfg: dict[str, Any],
    output: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute the complete walk-forward stability pipeline.

    Parameters
    ----------
    runtime_cfg : RuntimeConfig
        Runtime configuration (project_root, config_hash, etc.).
    wf_cfg : dict
        Walk-forward configuration: start_date, end_date, universe, settings,
        train_months=12, test_months=3, step_months=3, warmup_bars=260,
        stability_thresholds (optional overrides).
    output : Path
        Output directory (``data/research/walk_forward/<run_id>``).
    run_id : str, optional
        Explicit run ID. Auto-generated if None.

    Returns
    -------
    dict with keys: run_id, status, stability_classification, output_dir,
    manifest_path, total_folds, completed_folds, data_blocked, failed,
    fold_results, elapsed_seconds, total_closed_trades.

    Raises
    ------
    FileExistsError
        If *output* already contains a completed run and no matching run_id.
    RuntimeError
        If required peer modules are unavailable.
    """
    # -------------------------------------------------------------------
    # Validate peer modules
    # -------------------------------------------------------------------
    missing: list[str] = []
    if not _WF_AVAILABLE:
        missing.append("WalkForwardConfig")
    if not _PREFLIGHT_AVAILABLE:
        missing.append("preflight_fold")
    if not _OOS_AVAILABLE:
        missing.append("summarize_fold/aggregate_stability")
    if not _ENGINE_AVAILABLE:
        missing.append("BacktestEngine/HistoricalDataProvider")
    if missing:
        raise RuntimeError(
            f"Required peer modules unavailable: {', '.join(missing)}"
        )

    # -------------------------------------------------------------------
    # Resolve run_id and output directory
    # -------------------------------------------------------------------
    resolved_run_id = _safe_run_id(run_id)
    output_dir = output.resolve()
    manifest_path = output_dir / "manifest.json"
    latest_path = output_dir.parent / "latest.json"

    # Collision detection
    if output_dir.exists():
        existing = _list_non_empty(output_dir)
        if existing and not _is_incomplete_run(output_dir):
            if run_id and _has_matching_run_id(output_dir, run_id):
                pass  # Resumable — same run_id, already-tracked directory
            else:
                raise FileExistsError(
                    f"Output directory {output_dir} already contains results. "
                    f"Use a different --run-id or a clean --output path."
                )

    output_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------
    # Build WalkForwardConfig (peer: FlashFoldContracts)
    # -------------------------------------------------------------------
    project_root = runtime_cfg.project_root
    wf_config = WalkForwardConfig(
        start_date=wf_cfg["start_date"],
        end_date=wf_cfg["end_date"],
        universe=tuple(wf_cfg.get("universe", [])),
        settings=wf_cfg.get("settings", {}),
        train_months=wf_cfg.get("train_months", 12),
        test_months=wf_cfg.get("test_months", 3),
        step_months=wf_cfg.get("step_months", 3),
        warmup_bars=wf_cfg.get("warmup_bars", 260),
        stability_thresholds=wf_cfg.get("stability_thresholds"),
    )

    # -------------------------------------------------------------------
    # Generate folds plan (peer: FlashFoldContracts)
    # -------------------------------------------------------------------
    folds, partial_tail = generate_folds(wf_config)

    # -------------------------------------------------------------------
    # Pipeline state
    # -------------------------------------------------------------------
    fold_registry: dict[str, dict[str, Any]] = {}
    fold_summaries: list[dict[str, Any]] = []
    fold_errors: list[dict[str, Any]] = []
    benchmark_symbols = list(dict.fromkeys([
        wf_cfg.get("settings", {}).get("benchmark", "000300.SH"),
        *runtime_cfg.defaults.get("benchmarks", {}).values(),
    ]))

    # -------------------------------------------------------------------
    # Process each fold
    # -------------------------------------------------------------------
    for fold in folds:
        fold_id = str(fold["fold_id"])
        fold_dir = output_dir / "folds" / fold_id
        fold_dir.mkdir(parents=True, exist_ok=True)

        fold_state: dict[str, Any] = {
            "fold_id": fold_id,
            "status": "PENDING",
            "train_range": f"{fold['train_start']}..{fold['train_end_exclusive']}",
            "test_range": f"{fold['test_start']}..{fold['test_end_exclusive']}",
        }

        try:
            # --- Fresh provider per fold (prevents CA leakage) ---
            provider = HistoricalDataProvider(
                project_root,
                mcp=None,
                use_cache=wf_cfg.get("settings", {}).get("cache", True),
            )
            # Initialize universe for this fold
            fold_settings = settings_from(runtime_cfg, wf_cfg.get("settings", {}))
            uni = provider.load_universe_for_period(
                fold["test_start"],
                fold["test_end_exclusive"],
                fold_settings.universe_file,
                max_universe=fold_settings.max_universe,
                mode=fold_settings.universe_mode,
            )
            symbols = list(uni.symbols) if uni else list(wf_config.universe)
            if not symbols:
                raise RuntimeError(f"Empty universe for fold {fold_id}")

            # Augment fold with warmup/observation fields for preflight
            fold["warmup_end_exclusive"] = fold["train_start"]
            fold["warmup_start"] = str((datetime.strptime(fold["train_start"], "%Y-%m-%d").date() - __import__("datetime").timedelta(days=730)).isoformat())
            fold["observation_end_exclusive"] = fold["test_end_exclusive"]

            # --- Preflight (peer: FlashPreflight) returns (status, complete, reasons, inputs_info) ---
            preflight_status, preflight_complete, preflight_reasons, preflight_inputs = preflight_fold(
                provider,
                fold,
                fold_settings,
                benchmark_symbols=benchmark_symbols,
                calendar_dates=None,
            )
            preflight_status = str(preflight_status)

            if preflight_status != "READY":
                fold_state.update(
                status="DATA_BLOCKED",
                preflight={"status": preflight_status, "complete": preflight_complete, "reasons": preflight_reasons, "inputs_info": preflight_inputs},
                reasons=preflight_reasons,
                )
                fold_registry[fold_id] = fold_state
                fold_summaries.append({
                    "fold_id": fold_id,
                    "complete": False,
                    "status": "DATA_BLOCKED",
                    "n_closed": 0,
                    "n_open": 0,
                    "n_censored": 0,
                })
                _write_finite_json(fold_dir / "report.json", fold_state)
                _preflight_dict = {"status": preflight_status, "complete": preflight_complete, "reasons": preflight_reasons, "inputs_info": preflight_inputs}
                _write_finite_json(fold_dir / "preflight.json", _preflight_dict)
                continue

            # --- Execute fold (peer: FlashFoldExecution) ---
            engine = BacktestEngine(runtime_cfg, provider, fold_settings)
            eval_window = {
                "fold_id": fold["fold_id"],
                "entry_start": fold["test_start"],
                "entry_end_exclusive": fold["test_end_exclusive"],
                "observation_end_exclusive": fold.get(
                    "observation_end_exclusive", fold["test_end_exclusive"]
                ),
            }
            report = engine.run(symbols, evaluation_window=eval_window)
            report["fold_id"] = fold_id

            # --- Summarize (peer: FlashOOSStats) ---
            summary = summarize_fold(report)
            summary["fold_id"] = fold_id
            fold_summaries.append(summary)
            fold_registry[fold_id] = {
                "fold_id": fold_id,
                "status": "COMPLETED" if summary.get("complete", True) else "PARTIAL",
                "n_closed": summary.get("n_closed", 0),
                "evaluation_window": eval_window,
            }
            # --- Write fold artifacts (persistence module) ---
            write_fold_artifacts(fold_dir, report)

        except Exception as exc:
            fold_state["status"] = "RUN_FAILED"
            fold_state["error"] = f"{type(exc).__name__}: {exc}"
            import traceback as _tb
            fold_state["traceback"] = "".join(_tb.format_exc())
            fold_errors.append(fold_state)
            fold_registry[fold_id] = fold_state
            fold_summaries.append({
                "fold_id": fold_id,
                "complete": False,
                "status": "RUN_FAILED",
                "n_closed": 0,
                "n_open": 0,
                "n_censored": 0,
            })
            _write_finite_json(fold_dir / "report.json", fold_state)

    # -------------------------------------------------------------------
    # Aggregate stability (only from completed folds, peer: FlashOOSStats)
    # -------------------------------------------------------------------
    completed = [
        s for s in fold_summaries
        if s.get("status") in ("COMPLETED", "READY") and s.get("complete")
    ]
    stability_result: dict[str, Any] = {"classification": "NO_COMPLETED_FOLDS"}
    if completed:
        thresholds = wf_config.stability_thresholds
        stability_result = aggregate_stability(completed, thresholds=thresholds)

    # -------------------------------------------------------------------
    # Stitch partial tail info
    # -------------------------------------------------------------------
    tail_info: dict[str, Any] | None = None
    if partial_tail:
        tail_info = {
            "train_start": partial_tail.get("train_start"),
            "train_end_exclusive": partial_tail.get("train_end_exclusive"),
            "test_start": partial_tail.get("test_start"),
            "test_end_exclusive": partial_tail.get("test_end_exclusive"),
            "complete": partial_tail.get("complete", False),
        }

    # -------------------------------------------------------------------
    # Determine overall status
    # -------------------------------------------------------------------
    all_runnable = all(
        s.get("status") != "RUN_FAILED"
        for s in fold_summaries
        if s.get("status") not in ("DATA_BLOCKED",)
    )
    has_any_completed = any(s.get("complete") for s in fold_summaries)
    overall_status = (
        "COMPLETED" if (has_any_completed and all_runnable)
        else "PARTIAL" if has_any_completed
        else "FAILED"
    )

    # -------------------------------------------------------------------
    # Build methodology
    # -------------------------------------------------------------------
    methodology: dict[str, Any] = {
        "producer": "walk_forward_service.run_stability",
        "version": __version__,
        "engine_version": "2.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": resolved_run_id,
        "walk_forward_config": wf_config.to_dict(),
        "wf_config_hash": wf_config.canonical_hash(),
        "total_folds": len(folds),
        "completed_folds": len([s for s in fold_summaries if s.get("complete")]),
        "data_blocked_folds": len([
            s for s in fold_summaries if s.get("status") == "DATA_BLOCKED"
        ]),
        "failed_folds": len([
            s for s in fold_summaries if s.get("status") == "RUN_FAILED"
        ]),
        "partial_tail": tail_info,
        "fold_registry": fold_registry,
        "fold_errors": fold_errors,
        "no_trade_diagnostics": {
            "folds_with_zero_closed": [
                s["fold_id"] for s in fold_summaries
                if s.get("n_closed", 0) == 0
            ],
            "data_blocked_fold_ids": [
                s["fold_id"] for s in fold_summaries
                if s.get("status") == "DATA_BLOCKED"
            ],
            "failed_fold_ids": [
                s["fold_id"] for s in fold_summaries
                if s.get("status") == "RUN_FAILED"
            ],
        },
    }

    # -------------------------------------------------------------------
    # Build manifest
    # -------------------------------------------------------------------
    git_meta = get_git_metadata(project_root)
    manifest: dict[str, Any] = {
        "manifest_version": "1.0",
        "producer": "walk_forward_service.run_stability",
        "code_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": resolved_run_id,
        "source_sha": git_meta.get("git_commit_sha"),
        "dirty": not git_meta.get("tracked_tree_clean", False),
        "tracked_changes": git_meta.get("tracked_changes", []),
        "untracked_source_files": git_meta.get("untracked_source_files", []),
        "source_provenance_reason": git_meta.get("source_provenance_reason"),
        "config_hash": runtime_cfg.config_hash,
        "wf_config_hash": wf_config.canonical_hash(),
        "rule_hashes": _compute_rule_hashes(runtime_cfg),
        "env": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "dependencies": _resolve_dependencies(),
        "physical_inputs": _scan_physical_inputs(project_root),
        "settings": {
            "wf_config_keys": list(wf_cfg.keys()),
            "stability_thresholds": dict(wf_config.stability_thresholds or {}),
        },
        "fold_plan": {
            "total_folds": len(folds),
            "partial_tail": tail_info,
            "folds": [
                {
                    "fold_id": str(f["fold_id"]),
                    "train_range": f"{f['train_start']}..{f['train_end_exclusive']}",
                    "test_range": f"{f['test_start']}..{f['test_end_exclusive']}",
                    "complete": f.get("complete", True),
                }
                for f in folds
            ],
        },
    }

    # -------------------------------------------------------------------
    # Write outputs
    # -------------------------------------------------------------------
    _write_finite_json(output_dir / "manifest.json", manifest)
    _write_finite_json(output_dir / "methodology.json", methodology)
    write_oos_stability(
        output_dir / "oos_stability.json",
        output_dir / "oos_stability.csv",
        stability_result,
        completed or fold_summaries,
    )

    # -------------------------------------------------------------------
    # Latest pointer — only on non-failed overall
    # -------------------------------------------------------------------
    latest_data: dict[str, Any] = {
        "run_id": resolved_run_id,
        "status": overall_status,
        "path": str(output_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stability_classification": stability_result.get("classification"),
        "total_folds": len(folds),
        "completed_folds": methodology["completed_folds"],
    }

    if overall_status != "FAILED":
        _write_finite_json(latest_path, latest_data)
    else:
        _write_finite_json(output_dir / "diagnostics.json", {
            "status": overall_status,
            "fold_errors": fold_errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

    # -------------------------------------------------------------------
    # Return summary
    # -------------------------------------------------------------------
    return {
        "run_id": resolved_run_id,
        "status": overall_status,
        "stability_classification": stability_result.get("classification"),
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "total_folds": len(folds),
        "completed_folds": methodology["completed_folds"],
        "data_blocked": methodology["data_blocked_folds"],
        "failed": methodology["failed_folds"],
        "fold_results": [
            {
                "fold_id": fid,
                "status": info.get("status"),
                "n_closed": info.get("n_closed", 0),
            }
            for fid, info in fold_registry.items()
        ],
        "elapsed_seconds": 0.0,  # placeholder — real timing would wrap the loop
        "total_closed_trades": sum(
            s.get("n_closed", 0) for s in fold_summaries
        ),
    }
