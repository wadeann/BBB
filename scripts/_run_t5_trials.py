"""Trial5-based tuning: keep 35-symbol universe, vary params to fix drawdown."""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "config" / "walk_forward_trial5.yaml"
RESULTS_DIR = ROOT / "data" / "research" / "walk_forward"

# Strategy: Keep the trial5 relaxed thresholds. The most fixable key is
# BEAR::DISTRIBUTING::high_volume_breakout (positive exp & PF, 25 trades, fails on drawdown).
# Shorter max_holding_days should cap losses and reduce drawdown.
#
# Also try higher min_score to improve other keys' trade quality.

TRIALS = [
    # (name, min_score, max_positions, max_holding_days)
    # BEAR primary fixes: shorter holds to cap drawdown
    ("t5tune1_ms60_mp10_mh10", 60, 10, 10),
    ("t5tune2_ms60_mp10_mh8",  60, 10, 8),
    
    # Higher score to filter better trades (reduce negative expectancy)
    ("t5tune3_ms65_mp10_mh10", 65, 10, 10),
    ("t5tune4_ms65_mp10_mh8",  65, 10, 8),
    
    # Try more positions to spread concentration
    ("t5tune5_ms60_mp15_mh10", 60, 15, 10),
    
    # Try with 35 stocks + relaxed min_score for more trade volume
    ("t5tune6_ms55_mp10_mh10", 55, 10, 10),
    
    # Try original mh=15 with higher score
    ("t5tune7_ms65_mp10_mh15", 65, 10, 15),
    
    # Try min_score=70 to really filter for quality
    ("t5tune8_ms70_mp10_mh10", 70, 10, 10),
]


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

    tmp_cfg = ROOT / "config" / f"_t5tune_{name}.yaml"
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
    for name, ms, mp, mh in TRIALS:
        print(f"\n{'='*60}")
        print(f"TRIAL: {name}  (ms={ms}, mp={mp}, mh={mh})")
        print(f"{'='*60}")

        result = run_trial(name, ms, mp, mh)
        print(f"  exit_code: {result['exit_code']}")

        data = get_summary(name)
        if data:
            s = data.get("classification_summary", {})
            stable_ct = s.get("STABLE_CANDIDATE", 0)
            unstable_ct = s.get("UNSTABLE", 0)
            insuff_ct = s.get("INSUFFICIENT_DATA", 0)
            print(f"  STABLE={stable_ct}, UNSTABLE={unstable_ct}, INSUFF={insuff_ct}")

            # Show all STABLE keys
            keys = data.get("keys", {})
            for k, v in keys.items():
                if v.get("classification") == "STABLE_CANDIDATE":
                    print(f"  ★ {k}")

            # Show BEAR::DISTRIBUTING::high_volume_breakout specifically
            target = "BEAR::DISTRIBUTING::high_volume_breakout::1.0.0"
            if target in keys:
                v = keys[target]
                cl = v.get("classification", "?")
                reasons = {rk: rv for rk, rv in v.get("reasons", {}).items() if rv}
                r_str = "; ".join(f"{rk}={rv}" for rk, rv in reasons.items())
                
                # Compute economic metrics
                import statistics
                inf = [s for s in v.get("fold_summaries", []) if s.get("n_closed", 0) > 0]
                exp = [s["expectancy_pct"] for s in inf if s.get("expectancy_pct") is not None]
                pfs = [s["profit_factor"] for s in inf if s.get("pf_reason") == "FINITE" and s.get("profit_factor") is not None]
                dd = [s.get("max_drawdown_pct", 0) for s in inf if s.get("max_drawdown_pct") is not None]
                
                med_exp = statistics.median(exp) if exp else "?"
                med_pf = statistics.median(pfs) if pfs else "?"
                max_dd = min(dd) if dd else "?"
                
                print(f"  BEAR/hvb: {cl} inf={v['n_informative_folds']} closed={v['n_total_closed']} med_exp={med_exp} med_pf={med_pf} max_dd={max_dd}")
                if r_str:
                    print(f"    reasons: {r_str}")

            # Show count of keys that IMPROVED
            baseline_unstable = ["BULL_TREND::EMERGING::high_volume_breakout::1.0.0",
                "SIDEWAYS::DISTRIBUTING::triple_golden_cross::1.0.0",
                "SIDEWAYS::DISTRIBUTING::high_volume_breakout::1.0.0",
                "BULL_TREND::EMERGING::triple_golden_cross::1.0.0",
                "BEAR::DISTRIBUTING::high_volume_breakout::1.0.0",
                "BEAR::DISTRIBUTING::triple_golden_cross::1.0.0",
                "SIDEWAYS::EMERGING::triple_golden_cross::1.0.0",
                "BULL_TREND::EMERGING::single_bull_hold::1.0.0"]
            
            now_stable = sum(1 for k in baseline_unstable if k in keys and keys[k].get("classification") == "STABLE_CANDIDATE")
            if now_stable > 0:
                print(f"  ★ {now_stable}/{len(baseline_unstable)} baseline UNSTABLE keys now STABLE!")

        sys.stdout.flush()


if __name__ == "__main__":
    main()
