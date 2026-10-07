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
import tempfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..git_utils import get_git_metadata
from .. import __version__
from .data import ConsumedInputLedger, HistoricalDataUnavailable

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
    from ..backtest.oos_stability import summarize_fold, aggregate_stability, per_key_oos_stability, build_per_key_oos_artifact
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
    write_oos_stability, write_oos_per_key,
)


# ---------------------------------------------------------------------------
# Config rule keys for rule hashing
# ---------------------------------------------------------------------------


def _compute_rule_hashes(runtime_cfg: RuntimeConfig) -> dict[str, str]:
    """Bind rule names to explicit, required, redacted RuntimeConfig sources."""
    return runtime_cfg.rule_hashes


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()




def _assert_finite_json(raw: str) -> None:
    """Assert that a JSON string contains no NaN or Infinity tokens."""
    for tok in ("NaN", "Infinity", "-Infinity"):
        if tok in raw:
            raise ValueError(f"Non-finite JSON value detected: {tok}")


def _write_finite_json(path: Path, data: Any) -> None:
    from .walk_forward_persistence import write_finite_json
    write_finite_json(path, data)


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
    """Only a hash-bound completion marker publishes a generation."""
    try:
        manifest_path = directory / "manifest.json"
        marker_path = directory / "completion.json"
        if manifest_path.is_symlink() or marker_path.is_symlink():
            return True
        manifest = json.loads(manifest_path.read_text())
        marker = json.loads(marker_path.read_text())
        return not (manifest.get("overall_status") == "COMPLETED"
                    and manifest.get("generation_id")
                    and marker.get("generation_id") == manifest["generation_id"]
                    and marker.get("manifest_sha256") == hashlib.sha256(manifest_path.read_bytes()).hexdigest())
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return True


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
    *, mcp: Any = None,
) -> dict[str, Any]:
    """Execute the complete walk-forward stability pipeline.

    Parameters
    ----------
    runtime_cfg : RuntimeConfig
        Runtime configuration (project_root, config_hash, etc.).
    wf_cfg : dict
        Walk-forward configuration: start_date, end_date, universe, settings,
        train_months=12, test_months=3, step_months=3, warmup_bars=260,
        stability_thresholds (optional overrides), declared_keys (optional
        four-dimensional key universe, including keys without trades).
    output : Path
        Requested output directory. Reruns preserve it and install a sibling
        generation; consumers use returned output_dir or atomic latest.json.
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
    requested_output = output.absolute()
    if requested_output.is_symlink():
        raise ValueError("output directory must not be a symlink")
    latest_path = requested_output.parent / "latest.json"
    generation_id = uuid.uuid4().hex
    output_dir = requested_output

    # Collision detection
    if output_dir.exists():
        existing = _list_non_empty(output_dir)
        if existing and not _is_incomplete_run(output_dir):
            if run_id and _has_matching_run_id(output_dir, run_id):
                pass  # Same run ID is allowed, but the old generation is immutable.
            else:
                raise FileExistsError(
                    f"Output directory {output_dir} already contains results. "
                    f"Use a different --run-id or a clean --output path."
                )

    requested_output.parent.mkdir(parents=True, exist_ok=True)
    # Never mutate an existing output, including legacy or incomplete runs.
    final_output = (requested_output if not requested_output.exists() or not _list_non_empty(requested_output)
                    else requested_output.with_name(f"{requested_output.name}-{generation_id}"))
    output_dir = Path(tempfile.mkdtemp(prefix=f".tmp-{resolved_run_id}-", dir=requested_output.parent))
    manifest_path = output_dir / "manifest.json"

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
    fold_reports: list[dict[str, Any]] = []
    fold_errors: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    fold_registry: dict[str, Any] = {}
    consumed_universes: dict[str, dict[str, Any]] = {}
    preflight_commitments: dict[str, Any] = {}
    benchmark_symbols = list(dict.fromkeys([
        wf_cfg.get("settings", {}).get("benchmark", "000300.SH"),
        *runtime_cfg.defaults.get("benchmarks", {}).values(),
    ]))
    ledger = ConsumedInputLedger(project_root)

    # -------------------------------------------------------------------
    # Process each fold
    # -------------------------------------------------------------------
    for fold in folds:
        fold_id = str(fold["fold_id"])
        fold_dir = output_dir / "folds" / fold_id
        ledger.fold_id = fold_id
        ledger.phase = "initialization"
        fold_dir.mkdir(parents=True, exist_ok=True)

        fold_state: dict[str, Any] = {
            "fold_id": fold_id,
            "status": "PENDING",
            "train_range": f"{fold['train_start']}..{fold['train_end_exclusive']}",
            "test_range": f"{fold['test_start']}..{fold['test_end_exclusive']}",
        }

        try:
            if fold_id in fold_registry:
                raise ValueError(f"duplicate fold_id in expected plan: {fold_id}")
            fold["warmup_end_exclusive"] = fold["train_start"]
            calendar_path = project_root / "data/backtest/trading_grade_calendar.csv"
            calendar_dates = []
            if calendar_path.exists():
                calendar_rows = ledger.read(calendar_path, kind="acquisition_calendar",
                    parser=lambda raw: list(csv.DictReader(raw.decode("utf-8-sig").splitlines())))
                calendar_dates = sorted({str(row.get("date", "")).strip()
                    for row in calendar_rows if row.get("date")})
            preceding_dates = [d for d in calendar_dates if d < fold["train_start"]]
            required_warmup = max(0, int(wf_config.warmup_bars))
            # Planning uses actual calendar dates; preflight still verifies source
            # coverage and the actual raw/adjusted bar counts independently.
            fold["warmup_start"] = (preceding_dates[-required_warmup]
                if required_warmup and len(preceding_dates) >= required_warmup
                else preceding_dates[0] if required_warmup and preceding_dates
                else fold["train_start"])
            fold_settings = settings_from(runtime_cfg, wf_cfg.get("settings", {}))
            fold_settings.warmup_bars = required_warmup
            # Last test close can enter next open; max-holding close exits the
            # following open. Both execution sessions belong to observation.
            tail_required = max(0, int(getattr(fold_settings, "max_holding_days", 20))) + 2
            # Cap observation tail to available data end_date + 1 day (half-open).
            data_end_exclusive = (date.fromisoformat(wf_config.end_date) + timedelta(days=1)).isoformat()
            tail_dates_raw = [d for d in calendar_dates if d >= fold["test_end_exclusive"] and d < data_end_exclusive]
            tail_dates = tail_dates_raw[:tail_required]
            fold["observation_end_exclusive"] = (
                datetime.fromisoformat(tail_dates[-1]).date() + timedelta(days=1)).isoformat() if tail_dates else min(fold["test_end_exclusive"], data_end_exclusive)
            # No actual observation dates available → not a preflight blocker.
            fold["observation_tail_bars_required"] = len(tail_dates)
            acquisition_dates = [d for d in calendar_dates
                if fold["warmup_start"] <= d < fold["observation_end_exclusive"]]
            historical_max_bars = max(1, len(acquisition_dates))
            # --- Fresh provider per fold (prevents CA leakage) ---
            provider = HistoricalDataProvider(
                project_root,
                mcp=mcp,
                use_cache=False if mcp is not None else wf_cfg.get("settings", {}).get("cache", True),
                ledger=ledger,
                read_only_mcp=mcp is not None,
                strict_paid_source=bool(mcp is not None and not wf_cfg.get("settings", {}).get("allow_unverified_mcp", False)),
                historical_range=(fold["warmup_start"], fold["observation_end_exclusive"]),
                historical_max_bars=historical_max_bars,
                historical_request_end=acquisition_dates[-1] if acquisition_dates else None,
            )
            if getattr(provider, "ledger", None) is not ledger:
                ledger.append(kind="provider", status="unrecordable",
                              error="provider does not share the consumed input ledger")
            explicit_universe = list(wf_config.universe)
            if explicit_universe:
                symbols = explicit_universe
                uni = type("ExplicitUniverse", (), {
                    "symbols": symbols,
                    "source": "walk_forward_config",
                    "dataset_version": None,
                    "point_in_time": False,
                })()
            else:
                uni = provider.load_universe_for_period(
                    fold["test_start"],
                    fold["test_end_exclusive"],
                    fold_settings.universe_file,
                    max_universe=fold_settings.max_universe,
                    mode=fold_settings.universe_mode,
                )
                symbols = list(uni.symbols) if uni else []
            fold["universe"] = symbols
            consumed_universes[fold_id] = {
                "symbols": symbols, "universe_sha256": _stable_hash(symbols),
                "source": getattr(uni, "source", None),
                "dataset_version": getattr(uni, "dataset_version", None),
                "point_in_time": getattr(uni, "point_in_time", None),
                "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
                "universe_file": str(fold_settings.universe_file),
                "universe_mode": fold_settings.universe_mode,
                "max_universe": fold_settings.max_universe,
            }
            if not symbols:
                raise RuntimeError(f"Empty universe for fold {fold_id}")


            # --- Preflight (peer: FlashPreflight) returns (status, complete, reasons, inputs_info) ---
            ledger.phase = "preflight"
            preflight_status, preflight_complete, preflight_reasons, preflight_inputs = preflight_fold(
                provider,
                fold,
                fold_settings,
                benchmark_symbols=benchmark_symbols,
                calendar_dates=None,
            )
            preflight_status = str(preflight_status)
            preflight_commitments[fold_id] = preflight_inputs

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
                fold_reports.append({
                    "fold_id": fold_id,
                    "complete": False,
                    "status": "DATA_BLOCKED",
                    "trades": [],
                    "coverage": 1.0,
                })
                _write_finite_json(fold_dir / "report.json", fold_state)
                _preflight_dict = {"status": preflight_status, "complete": preflight_complete, "reasons": preflight_reasons, "inputs_info": preflight_inputs}
                _write_finite_json(fold_dir / "preflight.json", _preflight_dict)
                continue

            # --- Execute fold (peer: FlashFoldExecution) ---
            ledger.phase = "execution"
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
            # Validate fold_id in the engine report matches the expected plan
            report_fold_id = report.get("fold_id")
            if report_fold_id is None or str(report_fold_id) != str(fold_id):
                raise ValueError(
                    f"fold_id mismatch: engine returned {report_fold_id}, "
                    f"expected {fold_id}"
                )
            fold_reports.append(report)

            # --- Summarize (peer: FlashOOSStats) ---
            summary = summarize_fold(report)
            fold_summaries.append(summary)
            fold_registry[fold_id] = {
                "fold_id": fold_id,
                "status": summary.get("status", "COMPLETED") if summary.get("complete") else "PARTIAL",
                "n_closed": summary.get("n_closed", 0),
                "evaluation_window": eval_window,
            }
            # --- Write fold artifacts (persistence module) ---
            write_fold_artifacts(fold_dir, report)

        except Exception as exc:
            data_blocked = isinstance(exc, HistoricalDataUnavailable) or any(
                e['fold_id'] == fold_id and e['source_type'] == 'mcp'
                and e['status'] in {'error', 'empty', 'denied'} for e in ledger.events)
            fold_state['status'] = 'DATA_BLOCKED' if data_blocked else 'RUN_FAILED'
            fold_state['error'] = (str(exc) if isinstance(exc, HistoricalDataUnavailable)
                else type(exc).__name__ if data_blocked else f'{type(exc).__name__}: {exc}')
            if not data_blocked:
                import traceback as _tb
                fold_state['traceback'] = ''.join(_tb.format_exc())
            fold_errors.append(fold_state)
            fold_registry[fold_id] = fold_state
            fold_summaries.append({
                "fold_id": fold_id,
                "complete": False,
                "status": fold_state['status'],
                "n_closed": 0,
                "n_open": 0,
                "n_censored": 0,
            })
            fold_reports.append({
                "fold_id": fold_id,
                "complete": False,
                "status": fold_state['status'],
                "trades": [],
                "coverage": 1.0,
            })
            _write_finite_json(fold_dir / "report.json", fold_state)
        finally:
            events = [e for e in ledger.events if e["fold_id"] == fold_id]
            _write_finite_json(fold_dir / "consumed_inputs.json", {
                "events": events, "sha256": ledger.digest(events),
                "status": "VERIFIED" if events and all(e["verifiable"] for e in events) else "UNVERIFIABLE",
            })

    # -------------------------------------------------------------------
    # Aggregate the entire plan, retaining failed and incomplete folds.
    # -------------------------------------------------------------------
    completed = [
        s for s in fold_summaries
        if s.get("status") in ("COMPLETED", "READY") and s.get("complete")
    ]
    stability_result: dict[str, Any] = {"classification": "NO_COMPLETED_FOLDS"}
    if fold_summaries:
        thresholds = wf_config.stability_thresholds
        stability_result = aggregate_stability(fold_summaries, thresholds=thresholds)

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
        s.get("status") == "COMPLETED" and s.get("complete")
        for s in fold_summaries
    )
    has_any_completed = bool(completed)
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
    ledger_snapshot = ledger.snapshot(verify_files=True)
    manifest: dict[str, Any] = {
        "manifest_version": "1.0",
        "producer": "walk_forward_service.run_stability",
        "code_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": resolved_run_id,
        "generation_id": generation_id,
        "completion": overall_status == "COMPLETED",
        "artifact_path": "oos_stability.json",
        "consumed_universes": consumed_universes,
        "declared_keys": wf_cfg.get("declared_keys"),
        "preflight_input_commitments": preflight_commitments,
        "physical_provenance": {
            "status": ledger_snapshot["status"], "reasons": ledger_snapshot["reasons"],
        },
        "consumed_input_ledger": ledger_snapshot,
        "consumed_input_ledger_sha256": ledger_snapshot["sha256"],
        "physical_inputs": ledger_snapshot["physical_inputs"],
        "project_root": str(Path(project_root).absolute()),
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
        "physical_input_inventory": [],
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
        "overall_status": overall_status,
    }

    # -------------------------------------------------------------------
    # Write outputs (manifest only once, after all outputs are ready)
    # -------------------------------------------------------------------
    _write_finite_json(output_dir / "methodology.json", methodology)
    write_oos_stability(
        output_dir / "oos_stability.json",
        output_dir / "oos_stability.csv",
        stability_result,
        fold_summaries,
    )
    # -------------------------------------------------------------------
    # Per-four-key OOS stability (Phase 2B enablement gating)
    # -------------------------------------------------------------------
    expected_fold_ids = [str(f["fold_id"]) for f in folds]
    per_key_result = per_key_oos_stability(
        fold_reports,
        thresholds=wf_config.stability_thresholds,
        expected_fold_ids=expected_fold_ids,
        declared_keys=wf_cfg.get("declared_keys"),
    )
    per_key_artifact = build_per_key_oos_artifact(
        per_key_result,
        run_id=resolved_run_id,
        source_sha=git_meta.get("git_commit_sha", ""),
        config_hash=runtime_cfg.config_hash,
        wf_config_hash=wf_config.canonical_hash(),
        rule_hashes=_compute_rule_hashes(runtime_cfg),
        run_manifest=manifest,
    )
    per_key_hash = write_oos_per_key(output_dir / "oos_stability.json", per_key_artifact)

    # Compute SHA256 for every output file for manifest binding
    # -------------------------------------------------------------------
    output_files: dict[str, str] = {}
    for p in sorted(output_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel = str(p.relative_to(output_dir))
            output_files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest["output_files"] = output_files
    manifest["artifact_sha256"] = output_files.get(manifest["artifact_path"], "")

    # -------------------------------------------------------------------
    # Update manifest with per-key artifact hash
    # -------------------------------------------------------------------
    _write_finite_json(
        output_dir / "manifest.json",
        {**manifest, "oos_per_key_sha256": per_key_hash},
    )
    manifest["oos_per_key_sha256"] = per_key_hash

    # -------------------------------------------------------------------
    # Latest pointer — only on non-failed overall
    # -------------------------------------------------------------------
    latest_data: dict[str, Any] = {
        "run_id": resolved_run_id,
        "status": overall_status,
        "path": str(final_output),
        "generation_id": generation_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stability_classification": stability_result.get("classification"),
        "total_folds": len(folds),
        "completed_folds": methodology["completed_folds"],
    }

    if overall_status != "COMPLETED":
        _write_finite_json(output_dir / "diagnostics.json", {
            "status": overall_status,
            "fold_errors": [state for state in fold_registry.values()
                if state.get("status") != "COMPLETED"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    if overall_status == "COMPLETED":
        # Write the completion marker that binds manifest to published generation
        marker_manifest_path = output_dir / "manifest.json"
        _write_finite_json(output_dir / "completion.json", {
            "generation_id": generation_id,
            "run_id": resolved_run_id,
            "manifest_sha256": hashlib.sha256(marker_manifest_path.read_bytes()).hexdigest(),
        })
    os.replace(output_dir, final_output)
    output_dir = final_output
    manifest_path = output_dir / "manifest.json"
    if overall_status == "COMPLETED":
        _write_finite_json(latest_path, latest_data)

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
