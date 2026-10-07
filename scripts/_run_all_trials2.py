"""Batch tuning runner, round 2: try lower min_score values + more positions."""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "config" / "walk_forward.yaml"

# Lower-end values from the specified ranges:
# min_score: 55, 60, 65, 70
# max_holding_days: 10, 15, 20
# max_positions: 5, 10, 15

TRIALS = [
    # (name, min_score, max_positions, max_holding_days)
    ("tune2_min55_p5_h20", 55, 5, 20),
    ("tune2_min60_p5_h20", 60, 5, 20),
    ("tune2_min65_p5_h20", 65, 5, 20),
    ("tune2_min70_p10_h20", 70, 10, 20),
    ("tune2_min65_p10_h20", 65, 10, 20),
    ("tune2_min70_p15_h20", 70, 15, 20),
    ("tune2_min60_p10_h20", 60, 10, 20),
    ("tune2_min55_p10_h15", 55, 10, 15),
]

RESULTS_DIR = ROOT / "data" / "research" / "walk_forward"


def run_trial(name: str, min_score: float, max_positions: int, max_holding_days: int) -> dict:
    with CONFIG_SRC.open("r") as f:
        cfg = yaml.safe_load(f)

    cfg["settings"]["min_score"] = float(min_score)
    cfg["settings"]["max_positions"] = max_positions
    cfg["settings"]["max_holding_days"] = max_holding_days

    output_dir = RESULTS_DIR / name
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)

    tmp_cfg = ROOT / "config" / f"_trial2_{name}.yaml"
    with tmp_cfg.open("w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

    try:
        result = subprocess.run(
            [sys.executable, "scripts/run_walk_forward_stability.py",
             f"--config={tmp_cfg}",
             f"--output=data/research/walk_forward/{name}",
             f"--run-id={name}"],
            cwd=ROOT,
            capture_output=True, text=True, timeout=300,
        )
        return {
            "name": name,
            "settings": {"min_score": min_score, "max_positions": max_positions, "max_holding_days": max_holding_days},
            "exit_code": result.returncode,
        }
    finally:
        if tmp_cfg.exists():
            tmp_cfg.unlink()


def get_classification_summary(trial_name: str) -> dict | None:
    path = RESULTS_DIR / trial_name / "oos_stability.json"
    if not path.exists():
        return None
    with path.open("r") as f:
        data = json.load(f)
    return {
        "summary": data.get("classification_summary", {}),
        "keys": data.get("keys", {}),
    }


def main():
    for name, ms, mp, mh in TRIALS:
        print(f"\n{'='*60}")
        print(f"TRIAL: {name}  (min_score={ms}, max_positions={mp}, max_holding={mh})")
        print(f"{'='*60}")

        result = run_trial(name, ms, mp, mh)
        print(f"  exit_code: {result['exit_code']}")

        summary = get_classification_summary(name)
        if summary:
            s = summary["summary"]
            stable_ct = s.get('STABLE_CANDIDATE', 0)
            unstable_ct = s.get('UNSTABLE', 0)
            print(f"  STABLE={stable_ct}, UNSTABLE={unstable_ct}, INSUFF={s.get('INSUFFICIENT_DATA', 0)}")

            stable_keys = []
            unstable_details = []
            for key, val in summary["keys"].items():
                cl = val.get("classification", "")
                if cl == "STABLE_CANDIDATE":
                    stable_keys.append(key)
                elif cl == "UNSTABLE":
                    reasons = {k: v for k, v in val.get("reasons", {}).items() if v}
                    unstable_details.append((key, reasons))

            for k in stable_keys:
                print(f"    ✓ {k}")
            for k, reasons in unstable_details:
                r_str = "; ".join(f"{rk}={rv}" for rk, rv in reasons.items())
                print(f"    ✗ {k}  [{r_str}]")

        sys.stdout.flush()


if __name__ == "__main__":
    main()
