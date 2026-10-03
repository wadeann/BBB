#!/usr/bin/env python3
"""Standalone walk-forward stability runner.

Usage:
    python scripts/run_walk_forward_stability.py \\
        --root /path/to/project \\
        --config config/walk_forward.yaml \\
        --output data/research/walk_forward/<run_id> \\
        --run-id my_experiment_01

Exit codes:
    0  = completed
    1  = partial
    2  = failed
    3  = configuration error (bad args, missing config, collision)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from a_share_agent.config import load_config
from a_share_agent.backtest.walk_forward_service import run_stability


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_walk_forward_stability")
    p.add_argument("--root", default=None, help="Project root directory")
    p.add_argument(
        "--config", default="config/walk_forward.yaml",
        help="Walk-forward YAML config (default: config/walk_forward.yaml)",
    )
    p.add_argument(
        "--output", required=True,
        help=(
            "Output directory, e.g. data/research/walk_forward/<run_id>. "
            "Collision-safe: refuses if path already contains results."
        ),
    )
    p.add_argument(
        "--run-id", default=None,
        help="Explicit run identifier. Auto-generated if omitted.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve project root
    root = Path(args.root).resolve() if args.root else _project_root
    if not (root / "config").is_dir():
        print(f"ERROR: {root} lacks config/ directory", file=sys.stderr)
        sys.exit(3)

    # Load config
    runtime_cfg = load_config(root)

    # Load walk-forward YAML config
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = root / args.config
    if not config_path.exists():
        print(f"ERROR: config {config_path} not found", file=sys.stderr)
        sys.exit(3)

    import yaml
    with config_path.open("r", encoding="utf-8") as fh:
        wf_cfg = yaml.safe_load(fh) or {}
    wf_cfg.setdefault("start_date",
                      runtime_cfg.backtest.get("start_date", "2024-10-01"))
    wf_cfg.setdefault("end_date",
                      runtime_cfg.backtest.get("end_date", "2026-09-30"))
    wf_cfg.setdefault("train_months", 12)
    wf_cfg.setdefault("test_months", 3)
    wf_cfg.setdefault("step_months", 3)
    wf_cfg.setdefault("warmup_bars", 260)
    wf_cfg.setdefault("universe", [])
    wf_cfg.setdefault("settings", {})
    wf_cfg.setdefault("stability_thresholds", {})

    # Output directory
    output = Path(args.output)
    if not output.is_absolute():
        output = root / args.output

    # Run pipeline
    try:
        result = run_stability(runtime_cfg, wf_cfg, output, run_id=args.run_id)
    except FileExistsError as exc:
        print(f"COLLISION: {exc}", file=sys.stderr)
        sys.exit(3)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    status = result.get("status", "FAILED")
    sys.exit(0 if status == "COMPLETED" else 1 if status == "PARTIAL" else 2)


if __name__ == "__main__":
    main()
