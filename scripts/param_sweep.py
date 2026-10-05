#!/usr/bin/env python3
"""
Parameter sweep comparing backtest settings across 7 combos.
"""
import sys, os, time, json
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

os.chdir(_project_root)

from a_share_agent.config import load_project_env, load_config
load_project_env(_project_root)
cfg = load_config(_project_root)

from a_share_agent.factory import create_mcp_invoker
from a_share_agent.backtest.service import settings_from
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.engine import BacktestEngine

# ── MCP & Data ────────────────────────────────────────────────────────
mcp = create_mcp_invoker(cfg, backend='production')
provider = HistoricalDataProvider(_project_root, mcp, use_cache=True)

print("Loading universe (63 symbols)...", flush=True)
uni = provider.load_universe_for_period(
    '2026-07-01', '2026-09-30',
    'data/backtest/universe.txt',
    max_universe=63, mode='prefer_point_in_time'
)
symbols = uni.symbols
print(f"Universe: {len(symbols)} symbols\n", flush=True)

# ── Combos ────────────────────────────────────────────────────────────
combos = [
    ('A', 70, 'enabled', 20),
    ('B', 65, 'enabled', 20),
    ('C', 75, 'enabled', 20),
    ('D', 70, 'disabled', 20),
    ('E', 70, 'enabled', 10),
    ('F', 70, 'enabled', 30),
    ('G', 65, 'disabled', 20),
]

results = []
for label, ms, route, hold in combos:
    t0 = time.time()
    print(f"--- [{label}] min_score={ms}, route={route}, max_hold={hold} ---", flush=True)
    overrides = {
        'start_date': '2026-07-01',
        'end_date': '2026-09-30',
        'min_score': ms,
        'route_mode': route,
        'max_holding_days': hold,
    }
    s = settings_from(cfg, overrides)
    try:
        engine = BacktestEngine(cfg, provider, s)
        report = engine.run(symbols)
        m = report.get('metrics', {})
        elapsed = time.time() - t0
        results.append({
            'label': label,
            'min_score': ms,
            'route': route,
            'hold': hold,
            'return': m.get('total_return', 0),
            'trades': m.get('closed_trades', 0),
            'win_rate': m.get('win_rate', 0),
            'sharpe': m.get('sharpe', 0),
            'dd': m.get('max_drawdown', 0),
            'pf': m.get('profit_factor', 0),
            'time': round(elapsed, 1),
        })
        print(f"  Done: {elapsed:.0f}s  ret={m.get('total_return',0)*100:+.2f}%  trades={m.get('closed_trades',0)}", flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        results.append({'label': label, 'error': str(e)[:120]})
        print(f"  ERROR ({elapsed:.0f}s): {e}", flush=True)

# ── Results table ─────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("   参数优化回测对比 (2026-07-01 ~ 2026-09-30, 63 symbols)")
print("=" * 75)
print(f"{'组合':>4s} {'min':>3s} {'路由':>8s} {'持有':>4s} {'总收益':>10s} {'平仓':>5s} {'胜率':>8s} {'Sharpe':>8s} {'回撤':>10s} {'ProfitF':>8s} {'耗时':>6s}")
print("-" * 75)
for r in results:
    if 'error' in r:
        print(f"  {r['label']:>2s}  ERROR: {r['error']}")
    else:
        print(
            f"  {r['label']:>2s}  "
            f"{r['min_score']:3d}  "
            f"{r['route']:>8s}  "
            f"{r['hold']:4d}  "
            f"{r['return']*100:>+8.2f}%  "
            f"{r['trades']:5d}  "
            f"{r['win_rate']*100:>6.1f}%  "
            f"{r['sharpe']:>6.2f}  "
            f"{r['dd']*100:>8.2f}%  "
            f"{r['pf']:>6.2f}  "
            f"{r['time']:>5.0f}s"
        )
print("=" * 75)
