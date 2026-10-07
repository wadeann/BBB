"""Final targeted trials to push to 3+ STABLE keys."""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "config" / "walk_forward_trial5.yaml"
RESULTS_DIR = ROOT / "data" / "research" / "walk_forward"

# Key insights from prior runs:
# 1. mh=8 gives BULL hvb med_exp=-0.14% (very close to 0!)
# 2. BEAR triple_golden_cross has +0.78% med_exp but only 17 closed (needs 20)
# 3. BULL single_bull has PF=0.488 (needs 0.50) and dd=-0.275 (needs -0.25)

# Strategy: 
# - ms=58, mp=10, mh=8: slightly lower ms gives BEAR tgc more trades (+3 needed)
# - ms=60, mp=10, mh=6: even shorter holds to push BULL hvb to positive
# - ms=58, mp=10, mh=6: both benefits together
# - ms=60, mp=8, mh=8: fewer positions to reduce drawdown via less concurrent risk

TRIALS = [
    ("t5f_ms60_mp10_mh6", 60, 10, 6),
    ("t5f_ms58_mp10_mh8", 58, 10, 8),
    ("t5f_ms58_mp10_mh6", 58, 10, 6),
    ("t5f_ms60_mp8_mh8",  60, 8, 8),
]


def run_trial(name: str, ms: float, mp: int, mh: int):
    with CONFIG_SRC.open("r") as f:
        cfg = yaml.safe_load(f)
    cfg["settings"]["min_score"] = float(ms)
    cfg["settings"]["max_positions"] = mp
    cfg["settings"]["max_holding_days"] = mh

    out = RESULTS_DIR / name
    if out.exists():
        import shutil
        shutil.rmtree(out)

    tmp = ROOT / "config" / f"_t5f_{name}.yaml"
    with tmp.open("w") as f:
        yaml.dump(cfg, f)
    try:
        r = subprocess.run(
            [sys.executable, "scripts/run_walk_forward_stability.py",
             f"--config={tmp}", f"--output=data/research/walk_forward/{name}",
             f"--run-id={name}"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    finally:
        if tmp.exists():
            tmp.unlink()


def show(name: str):
    path = RESULTS_DIR / name / "oos_stability.json"
    if not path.exists():
        print(f"{name}: no results")
        return
    with path.open("r") as f:
        data = json.load(f)
    s = data.get("classification_summary", {})
    print(f"\n{'='*60}")
    print(f"{name}: STABLE={s.get('STABLE_CANDIDATE',0)} UNSTABLE={s.get('UNSTABLE',0)} INSUFF={s.get('INSUFFICIENT_DATA',0)}")

    keys = data.get("keys", {})
    for k, v in sorted(keys.items()):
        if v.get("classification") == "STABLE_CANDIDATE":
            inf = [x for x in v.get("fold_summaries",[]) if x.get("n_closed",0) > 0]
            exp = [x["expectancy_pct"] for x in inf if x.get("expectancy_pct") is not None]
            print(f'  ★ {k.split("::")[2][:25]} ({k.split("::")[0][:10]}::{k.split("::")[1][:12]}): inf={v["n_informative_folds"]} closed={v["n_total_closed"]} med_exp={statistics.median(exp):.4f}')

    # Show progress on target keys
    targets = ["BEAR::DISTRIBUTING::triple_golden_cross::1.0.0",
               "BULL_TREND::EMERGING::high_volume_breakout::1.0.0",
               "BULL_TREND::EMERGING::single_bull_hold::1.0.0",
               "SIDEWAYS::DISTRIBUTING::triple_golden_cross::1.0.0",
               "BULL_TREND::EMERGING::triple_golden_cross::1.0.0",
               "SIDEWAYS::EMERGING::triple_golden_cross::1.0.0"]
    for k in targets:
        if k in keys:
            v = keys[k]
            inf = [x for x in v.get("fold_summaries",[]) if x.get("n_closed",0) > 0]
            exp = [x["expectancy_pct"] for x in inf if x.get("expectancy_pct") is not None]
            pfs = [x["profit_factor"] for x in inf if x.get("pf_reason")=="FINITE" and x.get("profit_factor") is not None]
            dd = [x.get("max_drawdown_pct",0) for x in inf if x.get("max_drawdown_pct") is not None]
            medians = [x["median_trade_pct"] for x in inf if x.get("median_trade_pct") is not None]
            
            med_exp = statistics.median(exp) if exp else "?"
            med_pf = statistics.median(pfs) if pfs else "?"
            max_dd = min(dd) if dd else "?"
            pos_frac = sum(1 for m in medians if m > 0) / len(medians) if medians else "?"
            
            reasons = {rk: rv for rk, rv in v.get("reasons", {}).items() if rv}
            r_str = "; ".join(f"{rk}={rv}" for rk, rv in reasons.items())
            
            print(f'  {k.split("::")[2][:25]} ({k.split("::")[0][:10]}::{k.split("::")[1][:12]}): {v["classification"]} inf={v["n_informative_folds"]} closed={v["n_total_closed"]} me={med_exp} mPF={med_pf} dd={max_dd} pf={pos_frac}')
            if r_str:
                print(f'    [{r_str}]')

    sys.stdout.flush()


if __name__ == "__main__":
    for name, ms, mp, mh in TRIALS:
        print(f"Running {name} (ms={ms}, mp={mp}, mh={mh})...")
        sys.stdout.flush()
        run_trial(name, ms, mp, mh)
        show(name)
