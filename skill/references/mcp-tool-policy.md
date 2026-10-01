# MCP 工具调用策略 v3

本文件定义 53 个 MCP 的**分层、时段、成本和执行权限**。时间细节见 `daily-workflow.md`。

## A. 交易日与阶段控制
每个交易日首次运行、以及任何程序重启时：
- `mcp_intel_is_trading_day`
- `mcp_intel_trading_sessions`

交易阶段必须由 runtime session 结果确认，不能只根据本地时钟猜测。

健康检查：
- `mcp_intel_tdx_health`：盘前必查；故障时按需复查；
- `mcp_intel_market_source_health`：主源异常、最终报价冲突时查。

## B. 大盘/情绪工具频率
- `mcp_intel_fetch_market_health`：盘前、上午主入场前/中段、午间、下午主入场前、尾盘，可按15–30分钟刷新；
- `mcp_intel_get_mainline_lanes`：盘前、午间、下午关键阶段刷新，避免每分钟调用；
- `mcp_intel_get_limitup_ladder`：盘前读取最近完整数据，盘中在情绪明显变化或关键阶段更新；
- `mcp_intel_fetch_hot_signals`：盘中增量机会辅助，不作为全市场主扫描替代品。

## C. 股票池/批量扫描
全市场主扫描默认只在 `EOD_UNIVERSE_SCAN`：
- `mcp_intel_wencai_search`
- `mcp_intel_tdx_screener`
- `mcp_intel_screen_stocks`
- `mcp_intel_query_batch_data`

盘中不得为了“找更多机会”反复做全市场深度重扫；只能对活动候选池和持仓做批量刷新。

## D. 深度候选
只对通过初筛的少量标的：
- K线：`mcp_intel_tdx_kline`，备选 `mcp_intel_fetch_kline`
- 技术：`mcp_intel_get_technical_indicators`
- 筹码：`mcp_intel_get_chip_distribution`
- 资金：`mcp_intel_get_fund_flow`
- 财报/F10：`mcp_intel_tdx_f10` + 必要时 `mcp_intel_get_financial_report`
- 个股新闻：`mcp_intel_tdx_news` / `mcp_intel_search_news`

新闻/F10/筹码不得对数千只股票逐票调用。

## E. 板块
- 主线：`mcp_intel_get_mainline_lanes(top_n=5)`
- 板块历史：`mcp_intel_fetch_sector_history(code)`
- 热点：`mcp_intel_fetch_hot_signals()` 仅作辅助
- 股票所属板块：优先从 `mcp_intel_tdx_f10(module="basic")` 解析；不足时用固定问句 `mcp_intel_wencai_search`。

板块上下文设置 freshness；过期后必须刷新，禁止沿用昨日板块 gate 直接下单。

## F. 候选与自选股
- `mcp_intel_get_watchlist`：盘前读取；
- `mcp_intel_update_watchlist`：收盘深挖后维护 A/B 候选与原因；
- watchlist 仅是研究状态，不是 approved intent。

## G. 下单前实时行情
仅对准备生成订单的标的：
1. `mcp_intel_query_multi_source_quote` 必须调用；
2. 冲突时 `mcp_intel_market_source_health`；
3. 可用时 `mcp_intel_tdx_quotes` 作盘口补充。

报价冲突超过容差且无法消解 → `NO_ORDER`。

## H. 账户与风控
交易日盘前、午间、收盘后完整同步；盘中在每次新意图前至少确认必要的账户/订单状态。

完整顺序：
1. `mcp_exec_get_balance`
2. `mcp_exec_get_positions`
3. `mcp_exec_get_orders`
4. `mcp_exec_get_today_trades`
5. `mcp_exec_get_pnl`
6. `mcp_risk_daily_pnl`
7. `mcp_risk_get_blacklist`
8. `mcp_risk_check_intent` / `mcp_risk_batch_check`

风控拒绝不可被LLM覆盖。

## I. 幂等与重复单检查
在 `risk_check_intent` 之前：
- 查询 PENDING 订单；
- 查询今日成交；
- 检查同一稳定 intent key 是否已注册/执行；
- 默认同一 symbol 当日仅一次新入场；
- 止损后同日重入默认关闭。

调度器重跑不能导致重复下单。

## J. 模拟下单
只在 `mode=paper` 且当前 phase 允许新订单：
1. 生成稳定、唯一 intent_id；
2. 多源报价确认；
3. risk pass；
4. `mcp_exec_register_approved_intent`；
5. `mcp_exec_place_order`；
6. `mcp_exec_get_orders` / `mcp_exec_get_today_trades` 验证结果。

`live_proposal` 不得调用模拟 place_order 冒充实盘。

## K. 撤单
`mcp_exec_cancel_order` 仅在当前交易阶段允许撤单时使用。
- 09:20–09:25、14:57–15:00 不假设撤单有效；
- Skill 进一步在 09:19:30 和 14:56:30 提前进入保守冻结；
- 冻结后只能记录和告警，不创建“撤单已成功”的虚假状态。

## L. Jin10
`jin10` 只做宏观风险背景：
- `mcp_jin10_list_calendar`：盘前低频；
- `list/search flash/news`：重大突发事件时；
- `get_quote/kline`：确有跨资产判断需求时。

不进入每只A股默认评分，不因一条海外快讯自动清仓/满仓。

## M. Antigravity
只有用户明确要求 AGY/Antigravity 时才调用 `mcp_intel_analyze_stock_with_antigravity`。

## N. 工具失败策略
- 主源失败且有备源：切换并标记 `DEGRADED_DATA`；
- 关键报价冲突：`NO_ORDER`；
- 财报披露时点不明：该字段不评分；
- 板块映射不明：sector unknown，不猜；
- risk MCP 不可用：禁止新开仓；
- exec 状态不确定：先 `RECOVERY_SYNC`，禁止新开仓；
- 本地状态与 MCP 不一致：`DEGRADED_NO_ORDER`；
- 工具异常不得通过无限重试绕过权限或风控。


## O. DAILY_REVIEW 与改进闭环
DAILY_REVIEW 只读调用：
- `mcp_exec_get_balance`
- `mcp_exec_get_positions`
- `mcp_exec_get_orders`
- `mcp_exec_get_today_trades`
- `mcp_exec_get_pnl`
- `mcp_risk_daily_pnl`
以及本地持久化的 signal/route/context/event log。

不得在 DAILY_REVIEW 调用 place_order/register intent，也不得通过复盘直接写回 production 参数。
