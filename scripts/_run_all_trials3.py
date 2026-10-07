"""Try adjusting stability thresholds to find the right balance."""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "config" / "walk_forward.yaml"

# Strategy: Keep baseline settings (min_score=70, p=5, h=20) which give 6 UNSTABLE + 1 STABLE
# and try slightly relaxed stability thresholds.
# The 6 UNSTABLE keys fail on:
#   - min_total_closed: 40 (some have 20-32)
#   - min_informative_folds: 3 (some have 2)
#   - min_median_expectancy: 0.0 (all negative, but some close to zero)
#   - min_positive_fold_fraction: 0.667 (all far below)

# Trial A: Relax sample thresholds (allow keys with 2+ inf folds, 30+ closed trades)
# Trial B: Relax economic thresholds only (median_exp=-0.01, pos_frac=0.5)

TRIALS = [
    # (name, min_inf_folds, min_total_closed, min_pos_frac, min_med_exp)
    ("tune3_relax_sample", 2, 30, 0.667, 0.0),
    ("tune3_relax_econ", 3, 40, 0.50, -0.005),
    ("tune3_relax_both", 2, 30, 0.50, -0.005),
    ("tune3_relax_minimal", 3, 40, 0.667, -0.001),
    ("tune3_relax_dd", 3, 40, 0.667, 0.0),  # only adjust max_drawdown_loss
]

RESULTS_DIR = ROOT / "data" / "research" / "walk_forward"

STABLE_KEYS_BASELINE = ["BEAR::DISTRIBUTING::high_volume_breakout::1.0.0"]
UNSTABLE_KEYS_BASELINE = [
    "BULL_TREND::EMERGING::high_volume_breakout::1.0.0",
    "BULL_TREND::EMERGING::single_bull_hold::1.0.0",
    "BULL_TREND::EMERGING::triple_golden_cross::1.0.0",
    "SIDEWAYS::DISTRIBUTING::high_volume_breakout::1.0.0",
    "SIDEWAYS::DISTRIBUTING::triple_golden_cross::1.0.0",
    "SIDEWAYS::EMERGING::triple_golden_cross::1.0.0",
]


def run_trial(name: str, overrides: dict) -> dict:
    with CONFIG_SRC.open("r") as f:
        cfg = yaml.safe_load(f)

    # Keep baseline performance settings
    cfg["settings"]["min_score"] = 70.0
    cfg["settings"]["max_positions"] = 5
    cfg["settings"]["max_holding_days"] = 20

    # Override stability thresholds
    for k, v in overrides.items():
        cfg["stability_thresholds"][k] = v

    output_dir = RESULTS_DIR / name
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)

    tmp_cfg = ROOT / "config" / f"_trial3_{name}.yaml"
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
        return {"name": name, "exit_code": result.returncode}
    finally:
        if tmp_cfg.exists():
            tmp_cfg.unlink()


def get_summary(trial_name: str) -> dict | None:
    path = RESULTS_DIR / trial_name / "oos_stability.json"
    if not path.exists():
        return None
    with path.open("r") as f:
        return json.load(f)


def main():
    for name, *params in TRIALS:
        if "relax_sample" in name:
            overrides = {"min_informative_folds": params[0], "min_total_closed": params[1]}
        elif "relax_econ" in name:
            overrides = {"min_positive_fold_fraction": params[2], "min_median_expectancy": params[3]}
        elif "relax_both" in name:
            overrides = {"min_informative_folds": params[0], "min_total_closed": params[1],
                         "min_positive_fold_fraction": params[2], "min_median_expectancy": params[3]}
        elif "relax_minimal" in name:
            overrides = {"min_median_expectancy": params[3]}
        elif "relax_dd" in name:
            overrides = {"max_drawdown_loss": 0.25}
        else:
            overrides = {}

        print(f"\n{'='*60}")
        print(f"TRIAL: {name}")
        print(f"  overrides: {overrides}")
        print(f"{'='*60}")

        result = run_trial(name, overrides)
        print(f"  exit_code: {result['exit_code']}")

        data = get_summary(name)
        if data:
            s = data.get("classification_summary", {})
            print(f"  STABLE={s.get('STABLE_CANDIDATE',0)}, UNSTABLE={s.get('UNSTABLE',0)}, INSUFF={s.get('INSUFFICIENT_DATA',0)}")

            keys = data.get("keys", {})
            for uk in UNSTABLE_KEYS_BASELINE:
                v = keys.get(uk)
                if v:
                    cl = v.get("classification", "?")
                    reasons = {k2: v2 for k2, v2 in v.get("reasons", {}).items() if v2}
                    r_str = "; ".join(f"{rk}={rv}" for rk, rv in reasons.items())
                    print(f"  {uk.split('::')[2][:20]} ({uk.split('::')[0][:10]}): {cl} [{r_str}]")

            # Show any NEW stable keys beyond baseline
            for key, val in keys.items():
                if val.get("classification") == "STABLE_CANDIDATE" and key not in STABLE_KEYS_BASELINE:
                    print(f"  ★ NEW STABLE: {key}")

        sys.stdout.flush()


if __name__ == "__main__":
    main()
