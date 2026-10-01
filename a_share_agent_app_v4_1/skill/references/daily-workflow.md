# A股自动交易每日工作流 v3

所有时间均为 `Asia/Shanghai`。调度前必须先以 `mcp_intel_is_trading_day()` 和 `mcp_intel_trading_sessions()` 校验当天实际交易安排；运行时结果优先于本文默认时间。

## 1. 核心原则

1. 收盘后做全市场扫描，盘中只维护持仓、候选池和市场/板块增量变化。
2. 集合竞价主要观察；默认不在开盘/收盘不可撤单窗口创建新的策略性订单。
3. 每个 phase 明确“允许动作”和“禁止动作”，LLM不得跨 phase 自由发挥。
4. 所有新开仓都必须经过：最新行情 → market gate → sector gate → 个股信号 → 组合风险 → 多源报价 → risk MCP → intent → 执行。
5. 持仓退出优先于新开仓；账户/数据/风控异常优先于策略信号。
6. 任何重启都先进入 `RECOVERY_SYNC`。

## 2. 时段表

| 时间 | Phase | 核心任务 | 新开仓 |
|---|---|---|---|
| 08:45–09:00 | PREOPEN_PRECHECK | 交易日、数据源、余额、持仓、挂单、PnL、黑名单同步 | 禁止 |
| 09:00–09:15 | PREOPEN_CONTEXT | 大盘、主线、板块、昨晚候选、宏观日历 | 禁止 |
| 09:15–09:19:30 | OPEN_AUCTION_OBSERVE | 候选竞价观察、盘口、缺口 | 默认禁止 |
| 09:19:30–09:25 | OPEN_AUCTION_FREEZE | 进入不可撤单前冻结 | 禁止 |
| 09:25–09:30 | OPEN_RECONCILE | 开盘成交结果、订单/持仓对账 | 禁止 |
| 09:30–09:35 | OPEN_STABILIZE | 退出优先、撤异常单、更新市场状态 | 禁止 |
| 09:35–10:30 | ENTRY_WINDOW_AM | 上午主入场窗口 | 允许，需完整风控 |
| 10:30–11:20 | MORNING_MONITOR | 持仓管理、候选增量、热点/主线变化 | 受限允许 |
| 11:20–11:30 | MORNING_CLOSE_PROTECT | 未成交、风险仓、午间前对账 | 默认禁止 |
| 11:30–12:45 | MIDDAY_REVIEW | 上午复盘、下午候选重排 | 禁止 |
| 12:45–13:00 | PM_PRECHECK | 午间新闻、账户、黑名单、候选复核 | 禁止 |
| 13:00–13:05 | PM_STABILIZE | 下午开盘稳定窗 | 禁止 |
| 13:05–14:30 | ENTRY_WINDOW_PM | 下午主入场窗口 | 允许，需完整风控 |
| 14:30–14:56:30 | LATE_SESSION_MANAGE | 尾盘管理、减少不确定订单 | 高门槛受限 |
| 14:56:30–15:00 | CLOSE_AUCTION_FREEZE | 收盘竞价前冻结 | 禁止 |
| 15:00–15:15 | CLOSE_RECONCILE | 成交/挂单/余额/持仓/PnL对账 | 禁止 |
| 15:15–16:15 | EOD_UNIVERSE_SCAN | 全市场批量选股 | 禁止 |
| 16:15–17:00 | EOD_DEEP_DIVE | 50–200候选深挖，生成次日A/B池 | 禁止 |
| 20:30–21:00 | NIGHT_EVENT_CHECK | 持仓/A级候选公告与重大事件 | 禁止 |
| 21:00–21:30 | DAILY_REVIEW | 当日信号/风控/执行/结果归因，生成改进假设 | 禁止 |

## 3. 每个阶段的最小 MCP 集合

### PREOPEN_PRECHECK
`is_trading_day → trading_sessions → tdx_health → balance → positions → orders → today_trades → daily_pnl → blacklist`

### PREOPEN_CONTEXT
`benchmark kline → market_health → mainline_lanes → limitup_ladder → watchlist → finalists/positions news`

### AUCTION / OPEN
仅对持仓与候选池：`query_batch_data`，必要时 `tdx_quotes`。不全市场逐票深挖。

### ENTRY_WINDOW
`query_batch_data/tdx_quotes → sector refresh if stale → multi_source_quote → account/order dedupe → risk_check_intent → register_approved_intent → paper place_order → order receipt`

### MIDDAY
`orders → trades → balance → positions → pnl → market_health → mainline_lanes → candidate rerank`

### CLOSE_RECONCILE
`orders → trades → balance → positions → pnl → risk_daily_pnl`

### EOD_UNIVERSE_SCAN
`wencai/tdx_screener/screen_stocks → query_batch_data → batch filters → shortlist`

### EOD_DEEP_DIVE
`kline → technical → chip/fund_flow where useful → F10/financial → sector_history → finalists news`

## 4. 日线“收盘确认”规则

如果策略定义要求 `close > level`、日线实体、日量比等**最终收盘值**，则 15:00 前只能是 `PROVISIONAL`。

允许两种做法：
- `strict_close_confirmation=true`：收盘后确认，下一交易日再找入场；
- 独立尾盘策略：只有在已单独回测“14:xx近似收盘”的误差后，才允许在尾盘执行。

禁止把 14:45 的价格当作 15:00 收盘事实。

## 5. 重启恢复

任意时刻进程重启：

`RECOVERY_SYNC → 获取交易阶段 → balance/positions/orders/trades/pnl → 恢复intent状态 → 差异校验 → 当前phase`

如果本地状态与 MCP 返回无法解释地不一致，则进入 `DEGRADED_NO_ORDER`，只允许分析和告警。

## 6. 幂等与防重复

推荐稳定 intent key：

`YYYYMMDD|strategy_id|symbol|direction|phase|signal_version`

创建 intent 前至少检查：
- 当日是否已成交同方向；
- 是否已有 PENDING 订单；
- 是否刚止损；
- 是否处于 cooldown；
- 当前风险预算是否已经被其他订单占用。

默认同一 symbol 当日只允许一次新入场。

## 7. 盘中异常降级

- 行情主源失败但备源正常：`DEGRADED_DATA`，记录来源后可继续。
- 最终报价冲突超容差：`NO_ORDER`。
- risk MCP 不可用：禁止新开仓。
- exec 状态不确定：禁止新开仓，先 RECOVERY_SYNC。
- market/sector context 过期：先刷新，不得沿用旧 gate 下单。
- 新闻/宏观工具不可用：不影响纯技术扫描，但不能声称已完成事件风险核验。

## 8. 非交易日任务

可做：回测、walk-forward、参数稳定性、周复盘、异常成交审计、策略版本对比。

不可做：调用自动交易执行链路或生成“已成交”结果。


## 11. DAILY_REVIEW

21:00–21:30 默认执行受控日复盘：
- 汇总当日 signal/risk/order/trade/PnL；
- 按 strategy_family、market_regime、sector_strength 做归因；
- 计算 MFE/MAE、滑点、成交率与被阻断交易反事实；
- 产生 REVIEW_REPORT；
- 可产生 CHANGE_PROPOSAL，但 `production_effect=false`；
- 不允许修改生产参数或次日生效配置。

完整规则见 `continuous-improvement-loop.md`。


## v5 AUDIT_FINALIZE（21:30–21:40）
- 校验当日事件 hash chain；
- 检查 signal→risk→intent→order→receipt→review 是否存在审计缺口；
- 生成 daily manifest；
- 只允许本地审计写入，禁止交易写操作；
- 发现缺口时记录 `AUDIT_GAP` / `SYSTEM_ERROR`，不得篡改历史事件补洞。
