"""Batch tuning runner: try multiple parameter sets and summarize results."""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "config" / "walk_forward.yaml"

TRIALS = [
    # (name, min_score, max_positions, max_holding_days)
    ("tune_min75_hold10", 75, 5, 10),
    ("tune_min80_hold10", 80, 5, 10),
    ("tune_min80_hold15", 80, 5, 15),
    ("tune_min85_hold10", 85, 5, 10),
    ("tune_min75_hold15", 75, 5, 15),
    ("tune_min75_hold20", 75, 5, 20),
    ("tune_min70_hold10", 70, 5, 10),
    ("tune_min70_hold10_pos10", 70, 10, 10),
]

RESULTS_DIR = ROOT / "data" / "research" / "walk_forward"


def run_trial(name: str, min_score: float, max_positions: int, max_holding_days: int) -> dict:
    """Run one trial with modified config."""
    with CONFIG_SRC.open("r") as f:
        cfg = yaml.safe_load(f)

    cfg["settings"]["min_score"] = float(min_score)
    cfg["settings"]["max_positions"] = max_positions
    cfg["settings"]["max_holding_days"] = max_holding_days

    output_dir = RESULTS_DIR / name
    if output_dir.exists():
        # Remove to allow re-run
        import shutil
        shutil.rmtree(output_dir)

    # Write temp config
    tmp_cfg = ROOT / "config" / f"_trial_{name}.yaml"
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
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-2000:] if result.stderr else "",
        }
    finally:
        if tmp_cfg.exists():
            tmp_cfg.unlink()


def get_classification_summary(trial_name: str) -> dict | None:
    """Read oos_stability.json and extract classification summary."""
    path = RESULTS_DIR / trial_name / "oos_stability.json"
    if not path.exists():
        return None
    with path.open("r") as f:
        data = json.load(f)
    return {
        "summary": data.get("classification_summary", {}),
        "keys": data.get("keys", {}),
        "config_hash": data.get("config_hash", ""),
    }


def main():
    all_results = []

    for name, ms, mp, mh in TRIALS:
        print(f"\n{'='*60}")
        print(f"TRIAL: {name}  (min_score={ms}, max_positions={mp}, max_holding={mh})")
        print(f"{'='*60}")

        result = run_trial(name, ms, mp, mh)
        print(f"  exit_code: {result['exit_code']}")

        summary = get_classification_summary(name)
        if summary:
            s = summary["summary"]
            print(f"  classifications: STABLE={s.get('STABLE_CANDIDATE',0)}, UNSTABLE={s.get('UNSTABLE',0)}, INSUFF={s.get('INSUFFICIENT_DATA',0)}")

            # Show which keys are STABLE
            stable_keys = []
            unstable_keys = []
            for key, val in summary["keys"].items():
                cl = val.get("classification", "")
                if cl == "STABLE_CANDIDATE":
                    stable_keys.append(key)
                elif cl == "UNSTABLE":
                    unstable_keys.append((key, val.get("reasons", {})))

            if stable_keys:
                print(f"  STABLE_CANDIDATE keys ({len(stable_keys)}):")
                for k in stable_keys:
                    print(f"    ✓ {k}")
            if unstable_keys:
                print(f"  UNSTABLE keys ({len(unstable_keys)}):")
                for k, reasons in unstable_keys:
                    reasons_str = "; ".join(f"{rk}={rv}" for rk, rv in reasons.items() if rv)
                    print(f"    ✗ {k}  [{reasons_str}]")

        all_results.append(result)
        sys.stdout.flush()

    # Final summary
    print("\n\n" + "="*60)
    print("FINAL SUMMARY")
    print("="*60)
    for r in all_results:
        name = r["name"]
        s = get_classification_summary(name)
        if s:
            sm = s["summary"]
            stable_keys = [k for k, v in s["keys"].items() if v.get("classification") == "STABLE_CANDIDATE"]
            print(f"  {name}: STABLE={sm.get('STABLE_CANDIDATE',0)} UNSTABLE={sm.get('UNSTABLE',0)} INSUFF={sm.get('INSUFFICIENT_DATA',0)}  stable_keys={stable_keys}")


if __name__ == "__main__":
    main()
