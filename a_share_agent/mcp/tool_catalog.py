"""Canonical tool names supplied by the user, grouped by MCP service."""

INTEL_TOOLS = [
    "mcp_intel_query_data", "mcp_intel_query_batch_data", "mcp_intel_query_multi_source_quote",
    "mcp_intel_fetch_kline", "mcp_intel_get_chip_distribution", "mcp_intel_get_fund_flow",
    "mcp_intel_get_technical_indicators", "mcp_intel_get_financial_report", "mcp_intel_screen_stocks",
    "mcp_intel_search_news", "mcp_intel_webx_search", "mcp_intel_wencai_search",
    "mcp_intel_fetch_market_health", "mcp_intel_fetch_hot_signals", "mcp_intel_fetch_sector_history",
    "mcp_intel_get_watchlist", "mcp_intel_update_watchlist", "mcp_intel_get_limitup_ladder",
    "mcp_intel_get_mainline_lanes", "mcp_intel_get_rebound_candidates", "mcp_intel_get_rebound_pool",
    "mcp_intel_is_trading_day", "mcp_intel_trading_sessions", "mcp_intel_market_source_health",
    "mcp_intel_tdx_health", "mcp_intel_tdx_quotes", "mcp_intel_tdx_kline", "mcp_intel_tdx_f10",
    "mcp_intel_tdx_news", "mcp_intel_tdx_screener", "mcp_intel_analyze_stock_with_antigravity",
]
EXEC_TOOLS = [
    "mcp_exec_get_balance", "mcp_exec_get_positions", "mcp_exec_get_orders", "mcp_exec_get_today_trades",
    "mcp_exec_place_order", "mcp_exec_cancel_order", "mcp_exec_get_pnl", "mcp_exec_register_approved_intent",
    "mcp_exec_reconcile",
]
RISK_TOOLS = ["mcp_risk_check_intent", "mcp_risk_batch_check", "mcp_risk_daily_pnl", "mcp_risk_get_blacklist"]
JIN10_TOOLS = [
    "mcp_jin10_get_quote", "mcp_jin10_get_kline", "mcp_jin10_get_news", "mcp_jin10_list_news",
    "mcp_jin10_list_flash", "mcp_jin10_search_news", "mcp_jin10_search_flash", "mcp_jin10_list_calendar",
    "mcp_jin10_list_resources", "mcp_jin10_read_resource",
]
ALL_TOOLS = INTEL_TOOLS + EXEC_TOOLS + RISK_TOOLS + JIN10_TOOLS
assert len(ALL_TOOLS) == 54

CRITICAL_PAPER_TOOLS = {
    "mcp_intel_is_trading_day", "mcp_intel_trading_sessions", "mcp_intel_tdx_health",
    "mcp_intel_query_multi_source_quote", "mcp_exec_get_balance", "mcp_exec_get_positions",
    "mcp_exec_get_orders", "mcp_exec_get_today_trades", "mcp_exec_get_pnl",
    "mcp_exec_register_approved_intent", "mcp_exec_place_order", "mcp_risk_check_intent",
    "mcp_risk_daily_pnl", "mcp_risk_get_blacklist",
    "mcp_exec_reconcile",
}

# Optional research-grade historical tools. They are NOT part of the user's original
# 53-tool contract, so absence does not fail the production MCP catalog probe.
OPTIONAL_INTEL_TOOLS = [
    "mcp_intel_get_historical_universe",
    "mcp_intel_get_historical_security",
    "mcp_intel_get_historical_sector_membership",
    "mcp_intel_historical_bars",
]
