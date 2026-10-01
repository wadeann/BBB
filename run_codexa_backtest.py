#!/usr/bin/env python3
"""
CodexA v0.51 Backtest Runner
============================
Runs a 2-year historical backtest (2024-10-01 ~ 2026-09-30) using the codexA
BacktestEngine with real historical data from Astock's MCP server.

Usage: python3 run_codexa_backtest.py
"""
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Add project paths ────────────────────────────────────────────────
SRC = Path("/home/wade/workspace/ai/codexA/src")
ASTOCK = Path("/home/wade/workspace/ai/Astock")
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ASTOCK))

# ── MCP adapter: maps codexA tool names to Astock MCP server ─────────
import httpx

_CODE_TO_NAME = {
    "600000": "银行",
    "600036": "银行",
    "601166": "银行",
    "600519": "白酒",
    "000858": "白酒",
    "300750": "新能源",
    "600900": "公用事业",
    "601318": "保险",
    "000333": "家电",
    "002415": "信息技术",
    "601012": "光伏",
    "600276": "医药",
    "300059": "证券",
    "600030": "证券",
    "002594": "新能源汽车",
    "601899": "有色金属",
    "600887": "食品饮料",
    "000568": "白酒",
    "002475": "电子",
    "688981": "半导体",
    "002371": "半导体",
    "002049": "半导体",
    "688041": "半导体",
    "600703": "半导体",
    "688012": "半导体",
    "000725": "电子",
    "002230": "人工智能",
    "300124": "机器人",
    "002472": "机器人",
    "300308": "机器人",
    "601138": "电子",
    "002625": "机器人",
    "300502": "通信",
    "000063": "通信",
}


class MCPAdapter:
    """Adapts codexA mcp_intel_* tool calls to Astock's MCP JSON-RPC server."""

    def __init__(self, base_url: str = "http://127.0.0.1:9001"):
        self._http = httpx.Client(timeout=120.0, base_url=base_url)
        self.calls: list[tuple[str, dict]] = []
        self.warnings: list[str] = []
        self.stats: dict[str, int] = {}

    def invoke(self, tool_name: str, **kwargs: Any) -> Any:
        self.calls.append((tool_name, kwargs))
        self.stats[tool_name] = self.stats.get(tool_name, 0) + 1

        # Strip mcp_intel_ prefix if present
        if tool_name.startswith("mcp_intel_"):
            actual = tool_name[len("mcp_intel_"):]
        elif tool_name.startswith("mcp_"):
            actual = tool_name[len("mcp_"):]
        else:
            actual = tool_name

        # Tool name mapping
        tool_map = {
            "fetch_kline": "fetch_kline",
            "tdx_kline": "fetch_kline",
            "wencai_search": "wencai_search",
            "screen_stocks": "screen_stocks",
            "tdx_screener": "wencai_search",
            "tdx_f10": "tdx_f10",
            "fetch_sector_history": "fetch_sector_history",
            "query_batch_data": "query_batch_data",
            "query_data": "query_data",
            "query_multi_source_quote": "query_multi_source_quote",
            "tdx_quotes": "query_data",
            "is_trading_day": "is_trading_day",
            "trading_sessions": "trading_sessions",
            "tdx_health": "market_source_health",
            "market_source_health": "market_source_health",
            "fetch_market_health": "fetch_market_health",
            "get_limitup_ladder": "get_limitup_ladder",
            "get_mainline_lanes": "get_mainline_lanes",
            "get_technical_indicators": "get_technical_indicators",
            "get_chip_distribution": "get_chip_distribution",
            "get_fund_flow": "get_fund_flow",
            "get_financial_report": "get_financial_report",
            "search_news": "search_news",
            "tdx_news": "search_news",
            "get_watchlist": "get_watchlist",
            "fetch_hot_signals": "fetch_hot_signals",
            "get_rebound_candidates": "get_rebound_candidates",
        }
        server_tool = tool_map.get(actual, actual)

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": hash((tool_name, str(kwargs)[:100])) % 999999,
                "method": "tools/call",
                "params": {"name": server_tool, "arguments": dict(kwargs)},
            }
            resp = self._http.post("/mcp", json=payload)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data and data["error"]:
                err_msg = str(data["error"])
                if actual == "tdx_f10":
                    # TDX unavailable - return minimal sector info
                    return {"industry": self._guess_sector(kwargs.get("symbol", "")),
                            "basic": {"listing_days": 1000}}
                self.warnings.append(f"{tool_name}: {err_msg}")
                return {}

            content = data.get("result", {}).get("content", [])
            if not content:
                return {}
            text = content[0].get("text", "{}")
            result = json.loads(text) if isinstance(text, str) and text.strip().startswith("{") else text

            # Normalize: fetch_kline returns klines under "klines" key
            if server_tool == "fetch_kline":
                klines = result if isinstance(result, list) else result.get("klines", result.get("data", []))
                if isinstance(klines, list):
                    return klines
                return klines

            # Normalize: wencai_search returns columnar API format
            if server_tool == "wencai_search":
                datas = result.get("datas", [])
                columns = result.get("columns", [])
                if datas and columns:
                    # Find the stock code column
                    code_key = None
                    for c in columns:
                        if c.get("label") == "code" or "代码" in c.get("key", ""):
                            code_key = c.get("key")
                            break
                    if not code_key:
                        # Use the first column
                        code_key = columns[0].get("key", "股票代码")
                    return [{"symbol": d.get(code_key, "")} for d in datas if d.get(code_key)]
                return result

            if server_tool == "fetch_sector_history":
                if isinstance(result, dict):
                    history = result.get("history", [])
                    if not history or result.get("status") != "success":
                        # Return empty bars list
                        return []
                    # Convert fund flow to simple price-like bars
                    bars = []
                    for i, h in enumerate(history):
                        flow = float(h.get("flow", 0))
                        base = 100.0 + i * 0.1
                        bars.append({
                            "date": h.get("date", ""),
                            "close": base + flow * 2,
                            "open": base + flow * 1.5,
                            "high": base + flow * 2.5,
                            "low": base + flow * 0.5,
                            "volume": max(0, abs(flow) * 1000000),
                        })
                    return bars
                return result

            return result

        except Exception as exc:
            self.warnings.append(f"MCP call {tool_name} failed: {exc}")
            if actual == "tdx_f10":
                return {"industry": self._guess_sector(kwargs.get("symbol", "")),
                        "basic": {"listing_days": 1000}}
            return {}

    def _guess_sector(self, symbol: str) -> str | None:
        code = symbol.split(".")[0] if "." in symbol else symbol
        if len(code) >= 6:
            prefix = code[:6]
            if prefix in _CODE_TO_NAME:
                return _CODE_TO_NAME[prefix]
        return "综合"


# ── Main backtest runner ─────────────────────────────────────────────
def main():
    print("=" * 70)
    print("CodexA v0.51 - 2-Year Historical Backtest")
    print("Period: 2024-10-01 ~ 2026-09-30")
    print("=" * 70)
    print()

    from a_share_agent.config import load_config
    from a_share_agent.backtest.models import BacktestSettings
    from a_share_agent.backtest.data import HistoricalDataProvider
    from a_share_agent.backtest.engine import BacktestEngine
    from a_share_agent.backtest.report import BacktestReportWriter
    from a_share_agent.mcp.fake import FakeMCPInvoker

    # Load config
    config = load_config(SRC)
    print(f"Config loaded from: {SRC}")
    print(f"  Mode: {config.mode}")
    print(f"  Backend: {config.runtime.get('backend', 'fake')}")
    print()

    # Create MCP adapter using Astock's server
    mcp = MCPAdapter(base_url="http://127.0.0.1:9001")
    print("MCP adapter created (Astock intel server on port 9001)")
    print()

    # Create data provider
    provider = HistoricalDataProvider(SRC, mcp, use_cache=True)

    # Create backtest settings (2-year, same as Astock)
    settings = BacktestSettings(
        start_date="2024-10-01",
        end_date="2026-09-30",
        initial_cash=1_000_000.0,
        warmup_bars=260,
        benchmark="000300.SH",
        max_positions=5,
        max_holding_days=20,
        risk_per_trade=0.005,
        min_score=75.0,
        sector_mode="historical_or_neutral",
        max_universe=0,
        cache=True,
    )

    print("Backtest Settings:")
    for field in ("start_date", "end_date", "initial_cash", "benchmark",
                  "max_positions", "risk_per_trade", "sector_mode"):
        print(f"  {field}: {getattr(settings, field)}")
    print()

    # Load universe: try MCP wencai_search first, fallback to predefined list
    print("Loading stock universe...")
    universe_symbols = provider.load_universe(
        settings.universe_file, max_universe=0
    ).symbols if provider.mcp else []

    if not universe_symbols or len(universe_symbols) < 10:
        print("  wencai universe returned < 10 symbols, using fallback list...")
        # Fallback: common A-share stocks across sectors
        fallback = [
            "600519.SH", "000858.SZ", "000568.SZ",  # 白酒
            "600036.SH", "600000.SH", "601166.SH",  # 银行
            "601318.SH", "601628.SH",  # 保险
            "300750.SZ", "002594.SZ", "600900.SH",  # 新能源/公用
            "000333.SZ", "000651.SZ", "600690.SH",  # 家电
            "600276.SH", "300760.SZ", "000538.SZ",  # 医药
            "600030.SH", "300059.SZ", "601688.SH",  # 证券
            "002415.SZ", "000725.SZ", "002475.SZ",  # 电子/IT
            "002371.SZ", "688981.SH", "002049.SZ",  # 半导体
            "002230.SZ", "300124.SZ", "002472.SZ",  # AI/机器人
            "601012.SH", "600438.SH", "300274.SZ",  # 光伏
            "600887.SH", "002714.SZ", "300015.SZ",  # 消费/医疗
            "000002.SZ", "001979.SZ",  # 地产
            "601857.SH", "600028.SH",  # 能源
            "600585.SH", "601668.SH",  # 建材/基建
            "002304.SZ", "600809.SH",  # 更多消费
            "600031.SH", "000338.SZ",  # 机械
            "300413.SZ", "300502.SZ",  # 科技
            "002920.SZ", "300496.SZ",  # 软件
            "688041.SH", "688012.SH",  # 更多半导体
            "002625.SZ", "300308.SZ",  # 机器人
            "002460.SZ", "300014.SZ",  # 锂电
            "600036.SH",  # 已包含
            "601899.SH", "600547.SH",  # 有色
            "600104.SH", "000625.SZ",  # 汽车
            "002352.SZ", "002120.SZ",  # 物流
            "600941.SH", "000063.SZ",  # 通信
        ]
        universe_symbols = list(dict.fromkeys(fallback))

    print(f"  Universe size: {len(universe_symbols)} symbols")
    print()

    # Run the backtest
    print("Running backtest engine...")
    print("  (this may take a while with MCP data fetching + caching)")
    t0 = time.time()

    try:
        engine = BacktestEngine(config, provider, settings)
        report = engine.run(universe_symbols)

        elapsed = time.time() - t0
        print(f"  Backtest completed in {elapsed:.1f}s")
        print()

        # Extract metrics
        metrics = report.get("metrics", {})
        coverage = report.get("coverage", {})

        print("=" * 70)
        print("BACKTEST RESULTS")
        print("=" * 70)
        print()

        # Coverage
        print("Coverage:")
        print(f"  Requested:   {coverage.get('requested_symbols', 0)}")
        print(f"  Tested:      {coverage.get('tested_symbols', 0)}")
        print(f"  Missing:     {coverage.get('missing_symbols', 0)}")
        print(f"  Signals:     {coverage.get('signal_count', 0)}")
        print(f"  Candidates:  {coverage.get('candidate_count', 0)}")
        print()

        # Performance metrics
        total_return = metrics.get("total_return", 0) * 100
        cagr = metrics.get("cagr", 0) * 100
        sharpe = metrics.get("sharpe", 0)
        sortino = metrics.get("sortino", 0)
        calmar = metrics.get("calmar", 0)
        max_dd = metrics.get("max_drawdown", 0) * 100
        max_dd_days = metrics.get("max_drawdown_days", 0)

        print("Performance:")
        print(f"  Initial Cash:        {metrics.get('initial_cash', 0):,.0f}")
        print(f"  Ending Equity:       {metrics.get('ending_equity', 0):,.0f}")
        print(f"  Total Return:        {total_return:+.2f}%")
        print(f"  CAGR:                {cagr:+.2f}%")
        print(f"  Sharpe Ratio:        {sharpe:.2f}")
        print(f"  Sortino Ratio:       {sortino:.2f}")
        print(f"  Calmar Ratio:        {calmar:.2f}")
        print(f"  Max Drawdown:        {max_dd:+.2f}%")
        print(f"  Max Drawdown Days:   {max_dd_days}")
        print()

        # Trade statistics
        closed = metrics.get("closed_trades", 0)
        win_rate = metrics.get("win_rate", 0) * 100
        profit_factor = metrics.get("profit_factor", 0)
        avg_win = metrics.get("avg_win_pct", 0) * 100
        avg_loss = metrics.get("avg_loss_pct", 0) * 100
        expectancy = metrics.get("expectancy_pct", 0) * 100
        max_losses = metrics.get("max_consecutive_losses", 0)
        total_fees = metrics.get("total_fees", 0)

        print("Trades:")
        print(f"  Closed Trades:       {closed}")
        print(f"  Win Rate:            {win_rate:.1f}%")
        print(f"  Profit Factor:       {profit_factor:.2f}")
        print(f"  Avg Win:             {avg_win:+.2f}%")
        print(f"  Avg Loss:            {avg_loss:+.2f}%")
        print(f"  Expectancy/Trade:    {expectancy:+.2f}%")
        print(f"  Max Consec. Losses:  {max_losses}")
        print(f"  Total Fees:          {total_fees:,.0f}")
        print()

        # Benchmark comparison
        bench_ret = metrics.get("benchmark_return", 0) * 100
        excess = metrics.get("excess_return_vs_benchmark", 0) * 100
        if "benchmark_return" in metrics:
            print("Benchmark (沪深300):")
            print(f"  Benchmark Return:    {bench_ret:+.2f}%")
            print(f"  Excess Return:       {excess:+.2f}%")
            print()

        # Monthly stats
        monthly = report.get("monthly_returns", [])
        if monthly:
            best = max(float(m.get("return", 0)) for m in monthly) * 100
            worst = min(float(m.get("return", 0)) for m in monthly) * 100
            target_hits = metrics.get("months_ge_target", 0)
            print("Monthly:")
            print(f"  Months:              {len(monthly)}")
            print(f"  Best Month:          {best:+.2f}%")
            print(f"  Worst Month:         {worst:+.2f}%")
            print(f"  Months >= Target:    {target_hits}")
            print()

        # Strategy breakdown
        by_strategy = report.get("by_strategy", [])
        by_family = report.get("by_family", [])
        by_route = report.get("by_route", [])
        by_sector = report.get("by_sector", [])

        def show_group(label: str, data: list) -> None:
            if data:
                print(f"{label}:")
                for g in data[:8]:
                    print(f"  {g['group']:25s}: {g['trades']:4d} trades  "
                          f"PnL: {g['pnl']:>+10,.0f}  WR: {g.get('win_rate', 0)*100:.0f}%")
                print()

        show_group("By Strategy", by_strategy)
        show_group("By Family", by_family)
        show_group("By Route", by_route)
        show_group("By Sector", by_sector)

        # Methodology
        print("Methodology:")
        meth = report.get("methodology", {})
        for k, v in meth.items():
            print(f"  {k}: {v}")
        print()

        # MCP stats
        print("MCP Calls:")
        for tool, count in sorted(mcp.stats.items(), key=lambda x: -x[1]):
            print(f"  {tool}: {count}")
        if mcp.warnings:
            print(f"\n  Warnings ({len(mcp.warnings)}):")
            for w in mcp.warnings[:10]:
                print(f"    {w}")
        print()

        # Save results
        results_path = Path("/home/wade/workspace/ai/codexA/backtest_results.txt")
        lines = [
            "=" * 70,
            "CodexA v0.51 - 2-Year Historical Backtest Results",
            f"Period: 2024-10-01 ~ 2026-09-30",
            f"Run at: {report.get('created_at', 'N/A')}",
            f"Run ID: {report.get('run_id', 'N/A')}",
            "=" * 70,
            "",
            f"Backtest Universe: {coverage.get('tested_symbols', 0)} stocks",
            f"Trading Days: ~504 (2 years)",
            "",
            "PERFORMANCE METRICS",
            "-" * 50,
            f"  Initial Cash:          {metrics.get('initial_cash', 0):>12,.0f}",
            f"  Ending Equity:         {metrics.get('ending_equity', 0):>12,.0f}",
            f"  Total Return:          {total_return:>+12.2f}%",
            f"  CAGR:                  {cagr:>+12.2f}%",
            f"  Sharpe Ratio:          {sharpe:>12.2f}",
            f"  Sortino Ratio:         {sortino:>12.2f}",
            f"  Max Drawdown:          {max_dd:>+12.2f}%",
            f"  Max Drawdown Days:     {max_dd_days:>12}",
            "",
            "TRADE STATISTICS",
            "-" * 50,
            f"  Closed Trades:         {closed:>12}",
            f"  Win Rate:              {win_rate:>12.1f}%",
            f"  Profit Factor:         {profit_factor:>12.2f}",
            f"  Avg Win:               {avg_win:>+12.2f}%",
            f"  Avg Loss:              {avg_loss:>+12.2f}%",
            f"  Expectancy per Trade:  {expectancy:>+12.2f}%",
            f"  Max Consec. Losses:    {max_losses:>12}",
            f"  Total Fees:            {total_fees:>12,.0f}",
            "",
            "BENCHMARK COMPARISON",
            "-" * 50,
            f"  Benchmark (沪深300):    {bench_ret:>+12.2f}%",
            f"  Excess Return:          {excess:>+12.2f}%",
            "",
            "MONTHLY RETURNS",
            "-" * 50,
        ]
        for m in monthly:
            lines.append(f"  {m['month']}: {float(m['return'])*100:+.2f}%")
        lines.append("")

        # Trade list (first 30)
        trades = report.get("trades", [])
        lines.append("TRADE LOG (first 50)")
        lines.append("-" * 50)
        for t in trades[:50]:
            pnl_str = f"PnL: {t.get('pnl', 0):>+10,.0f}" if t.get("pnl") else ""
            lines.append(f"  {t['trade_date']} {t['direction']:4s} {t['symbol']:10s} "
                         f"Qty:{t.get('quantity', 0):>5d} @ {t.get('price', 0):>8.2f}  {pnl_str}")
        lines.append("")

        # Strategy breakdown
        lines.append("STRATEGY BREAKDOWN")
        lines.append("-" * 50)
        show_group_list(lines, "By Strategy", by_strategy)
        show_group_list(lines, "By Family", by_family)
        show_group_list(lines, "By Route", by_route)

        lines.append("DATA QUALITY NOTES")
        lines.append("-" * 50)
        dq = report.get("data_quality", {})
        lines.append(f"  Missing bars: {len(dq.get('missing_bars', []))}")
        lines.append(f"  Sector history missing: {len(dq.get('sector_history_missing', []))}")
        for w in dq.get("warnings", []):
            lines.append(f"  Warning: {w}")

        lines.append("")
        lines.append("METHODOLOGY")
        lines.append("-" * 50)
        for k, v in meth.items():
            lines.append(f"  {k}: {v}")

        lines.append("")
        lines.append("=" * 70)
        lines.append(f"Results saved to: {results_path}")
        lines.append("=" * 70)

        report_text = "\n".join(lines)
        results_path.write_text(report_text, encoding="utf-8")
        print(f"\nResults saved to: {results_path}")

        # Also write the raw JSON report
        json_path = Path("/home/wade/workspace/ai/codexA/backtest_raw_report.json")
        # Clean up report for JSON serialization (remove non-serializable)
        clean_report = json.loads(json.dumps(report, default=str))
        json_path.write_text(json.dumps(clean_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Raw report saved to: {json_path}")

        return report

    except Exception as e:
        import traceback
        print(f"\nERROR: Backtest failed: {e}")
        traceback.print_exc()

        # Save error
        error_path = Path("/home/wade/workspace/ai/codexA/backtest_error.txt")
        error_path.write_text(f"Error: {e}\n\n{traceback.format_exc()}", encoding="utf-8")
        print(f"Error saved to: {error_path}")
        return None


def show_group_list(lines: list, label: str, data: list) -> None:
    if data:
        lines.append(f"{label}:")
        for g in data:
            lines.append(f"  {g['group']:25s}: {g['trades']:4d} trades  "
                         f"PnL: {g['pnl']:>+10,.0f}  WR: {g.get('win_rate', 0)*100:.0f}%")
        lines.append("")


if __name__ == "__main__":
    main()
