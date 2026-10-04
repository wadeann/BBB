"""Walk-forward artifact persistence module.

Ownership: FlashIntegration (Task 2A.5). Does not touch report.py.
All file I/O for the walk-forward stability pipeline.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _assert_finite_json(raw: str) -> None:
    for tok in ("NaN", "Infinity", "-Infinity"):
        if tok in raw:
            raise ValueError(f"Non-finite JSON value detected: {tok}")


def write_finite_json(path: Path, data: Any) -> None:
    raw = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    _assert_finite_json(raw)
    path.write_text(raw, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()})


def write_fold_artifacts(fold_dir: Path, report: dict[str, Any]) -> None:
    fold_dir.mkdir(parents=True, exist_ok=True)
    write_finite_json(fold_dir / "report.json", report)
    write_csv(fold_dir / "trades.csv", report.get("trades", []))
    write_csv(fold_dir / "matrix.csv", report.get("regime_pattern_matrix", []))


def write_oos_stability(json_path: Path, csv_path: Path, stability: dict, summaries: list) -> None:
    write_finite_json(json_path, stability)
    rows = []
    for s in summaries:
        rows.append(dict(fold_id=s.get("fold_id"), n_closed=s.get("n_closed", 0),
            n_open=s.get("n_open", 0), n_censored=s.get("n_censored", 0),
            win_rate=s.get("win_rate"), expectancy_pct=s.get("expectancy_pct"),
            median_trade_pct=s.get("median_trade_pct"), profit_factor=s.get("profit_factor"),
            pf_reason=s.get("pf_reason"), max_drawdown_pct=s.get("max_drawdown_pct"),
            avg_mfe_pct=s.get("avg_mfe_pct"), avg_mae_pct=s.get("avg_mae_pct")))
    rows.append(dict(fold_id="__aggregate__", n_closed=stability.get("n_total_closed"),
        classification=stability.get("classification"),
        n_observed_folds=stability.get("n_observed_folds"),
        n_informative_folds=stability.get("n_informative_folds"),
        n_failed_folds=stability.get("n_failed_folds"),
        pooled_median_trade_pct=stability.get("pooled_median_trade_pct"),
        fold_median_trade_pct=stability.get("fold_median_trade_pct"),
        fold_median_expectancy_pct=stability.get("fold_median_expectancy_pct"),
        fold_median_profit_factor=stability.get("fold_median_profit_factor"),
        max_fold_return_concentration=stability.get("max_fold_return_concentration")))
    write_csv(csv_path, rows)

def write_oos_per_key(path: Path, artifact: dict) -> str:
    """Write per-key OOS artifact to JSON and return its content_hash.

    Args:
        path: output JSON path (e.g. ``output_dir / "oos_per_key.json"``).
        artifact: artifact dict from build_per_key_oos_artifact().

    Returns:
        The content_hash string recorded in the artifact, for manifest binding.
    """
    write_finite_json(path, artifact)
    return str(artifact.get("content_hash", ""))
