#!/usr/bin/env python3
"""A股选股系统终端仪表盘

Usage:
  cd src && python3 scripts/dashboard.py
  cd src && python3 scripts/dashboard.py --refresh 30

Shows: market regime, top candidates, recent trades, account summary
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def clear():
    os.system("clear" if os.name == "posix" else "cls")


def run_cmd(cmd, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or Path.cwd())
    return result.stdout, result.stderr, result.returncode


def get_stock_pick(limit=10, cwd=None):
    out, err, rc = run_cmd(
        [sys.executable, "-m", "a_share_agent.cli", "stock-pick", "--json",
         "--limit", str(limit)],
        cwd=cwd,
    )
    if rc != 0:
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, str(e)


def get_backtest_report(cwd=None):
    """Get latest saved backtest report if available"""
    reports_dir = Path(cwd or ".") / "data" / "backtest"
    if not reports_dir.exists():
        return None
    latest = reports_dir / "latest.json"
    if latest.exists():
        try:
            with open(latest) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def render_dashboard(data, backtest=None):
    clear()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\033[1;36m{'='*70}\033[0m")
    print(f"\033[1;36m  A股 选股系统 仪表盘  |  {now}\033[0m")
    print(f"\033[1;36m{'='*70}\033[0m")

    if data is None:
        print(f"\n  \033[1;31m⚠ MCP 连接失败，显示缓存数据\033[0m\n")
        return

    # Market regime
    market = data.get("market", {})
    regime = market.get("market_regime", "?")
    expanded = market.get("regime", "?")
    sentiment = market.get("sentiment_phase", "?")
    as_of = market.get("as_of", "?")

    # Color based on regime
    regime_colors = {
        "risk_on": "\033[1;32m",  # green
        "risk_off": "\033[1;31m",  # red
        "neutral": "\033[1;33m",  # yellow
    }
    rc = regime_colors.get(regime, "\033[1;37m")

    print(f"\n  \033[1m市场状态\033[0m  {rc}{regime}\033[0m ({expanded})  |  情绪: {sentiment}  |  as_of: {as_of}")
    print(f"  {'─'*60}")

    # Backtest summary if available
    if backtest:
        m = backtest.get("metrics", {})
        total_ret = m.get("total_return", 0) * 100
        ret_str = f"+{total_ret:.1f}%" if total_ret > 0 else f"{total_ret:.1f}%"
        ret_green = f"\033[1;32m{ret_str}\033[0m"
        ret_red = f"\033[1;31m{ret_str}\033[0m"
        ret_color = ret_green if total_ret > 0 else ret_red
        print(
            f"  \033[1m最近回测\033[0m  收益: {ret_color}"
            f"  |  交易: {m.get('closed_trades', 0)}笔"
            f"  |  胜率: {m.get('win_rate', 0)*100:.0f}%"
            f"  |  Sharpe: {m.get('sharpe', 0):.2f}"
            f"  |  回撤: {m.get('max_drawdown', 0)*100:.2f}%"
        )
        print(f"  {'─'*60}")

    # Candidates
    candidates = data.get("candidates", [])
    total = data.get("total_universe", 0)
    count = data.get("candidate_count", 0)

    print(f"\n  \033[1m今日选股\033[0m  候选: \033[1;33m{count}\033[0m/{total} 只")
    print(f"  {'─'*60}")
    if candidates:
        print(
            f"  {'评分':>4}  {'代码':10s} {'名称':12s} {'板块':14s} {'策略':25s} {'路由':18s} {'共振':6s}")
        print(f"  {'─'*60}")
        for c in candidates[:10]:
            score = c.get("score", 0)
            score_color = "\033[1;32m" if score >= 85 else (
                "\033[1;33m" if score >= 75 else "\033[0m")
            resonance = c.get("resonance", {})
            res_str = "\033[1;32m✓\033[0m" if resonance.get("pass") else "\033[1;31m✗\033[0m" if not resonance.get(
                "pass") else "?"
            print(
                f"  {score_color}{score:4.0f}\033[0m"
                f"  {c.get('symbol', '?'):10s}"
                f"  {str(c.get('stock_name', '') or '?')[:12]:12s}"
                f"  {str(c.get('sector', '') or '?')[:14]:14s}"
                f"  {c.get('strategy', '?'):25s}"
                f"  {c.get('route_id', '?'):18s}"
                f"  {res_str}"
            )
    else:
        print(f"  \033[1;33m暂无候选股票\033[0m")

    # Market detail
    print(f"\n  \033[1m市场详情报\033[0m")
    print(f"  {'─'*60}")
    for sym, bm in market.get("benchmarks", {}).items():
        bm_regime = bm.get("market_regime", "?")
        print(f"  {sym:12s}  {bm_regime:10s}  {bm.get('regime','?'):12s}  "
              f"{bm.get('sentiment_phase','?'):12s}  confidence={bm.get('regime_confidence',0):.1f}")

    print(f"\n  \033[1;36m{'─'*60}\033[0m")
    print(f"  刷新: Ctrl+C 退出 | 自动每60秒刷新")
    print(f"\033[1;36m{'='*70}\033[0m")


def main():
    p = argparse.ArgumentParser(description="A股选股系统终端仪表盘")
    p.add_argument("--refresh", type=int, default=60, help="auto-refresh seconds (0=once)")
    p.add_argument("--limit", type=int, default=10, help="max candidates")
    p.add_argument("--cwd", default=os.path.join(os.path.dirname(__file__), ".."))
    args = p.parse_args()

    cwd = os.path.abspath(args.cwd)
    backtest = get_backtest_report(cwd=cwd)

    try:
        while True:
            data, err = get_stock_pick(limit=args.limit, cwd=cwd)
            render_dashboard(data, backtest)

            if args.refresh <= 0:
                break

            try:
                time.sleep(args.refresh)
            except KeyboardInterrupt:
                print("\n  退出")
                break
    except KeyboardInterrupt:
        print("\n  退出")


if __name__ == "__main__":
    main()
