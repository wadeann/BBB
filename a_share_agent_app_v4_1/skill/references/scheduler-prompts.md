# Scheduler → LLM 调用模板 v3

调度器不要每天重新解释整套策略，只需加载 `SKILL.md`，然后按时段发送一个短任务对象。

## 1. 通用调用模板

```text
执行 A股自动交易计划任务。

mode: paper
exchange_timezone: Asia/Shanghai
phase: {{PHASE}}
as_of: {{ISO_DATETIME}}
run_id: {{RUN_ID}}

严格遵守 SKILL.md、config/defaults.yaml、config/schedule.yaml。
先确认 phase 与 mcp_intel_trading_sessions() 一致。
只执行该 phase 允许的任务，不跨阶段扩大权限。
任何新订单必须经过完整行情校验、去重、risk MCP、intent 注册。
如果关键数据/账户/风控状态不可靠，输出 DEGRADED_NO_ORDER。
返回结构化结果，包括：phase、market_regime、actions、tool_trace、risk_results、orders、state_updates、next_phase_hint。
```

## 2. PREOPEN_PRECHECK

```text
phase=PREOPEN_PRECHECK
完成交易日/交易时段/数据源健康、余额、持仓、挂单、当日成交、PnL、黑名单同步。
禁止新开仓。发现本地与MCP状态不一致时进入RECOVERY_SYNC或DEGRADED_NO_ORDER。
```

## 3. PREOPEN_CONTEXT

```text
phase=PREOPEN_CONTEXT
构建今日第一版 market_regime、主线和板块环境；复核昨晚A/B候选及现有持仓事件风险。
输出 candidate_plan_am 和 position_exit_watch。
禁止生成可执行买单。
```

## 4. ENTRY_WINDOW_AM / PM

```text
phase={{ENTRY_WINDOW_AM_OR_PM}}
只处理已经进入活动候选池的标的和已有持仓。
先刷新 market/sector gate；验证个股信号；检查账户、当日成交和PENDING订单以防重复；最终价格必须多源核验；随后调用risk MCP。
只有 risk=PASS 且 mode=paper 时，才能注册approved intent并调用模拟place_order。
每次下单后读取订单回执。
```

## 5. MORNING_MONITOR / LATE_SESSION_MANAGE

```text
phase={{PHASE}}
持仓管理优先。允许对候选池做增量更新，禁止全市场深度重扫。
强退出覆盖入场；同一股票当日重复入场默认禁止。
若处于尾盘，对新仓提高门槛，且不得把未完成的日线当作最终收盘信号。
```

## 6. CLOSE_RECONCILE

```text
phase=CLOSE_RECONCILE
不选股，不下单。核对订单、成交、余额、持仓和PnL；记录滑点、未成交、撤单和risk reject原因。
```

## 7. EOD_UNIVERSE_SCAN

```text
phase=EOD_UNIVERSE_SCAN
使用正式收盘数据做全市场批量扫描。先筛选/批量行情，再硬过滤和大盘/板块过滤；压缩到少量shortlist。
禁止逐票新闻/F10/筹码/多源报价，禁止下单。
```

## 8. EOD_DEEP_DIVE

```text
phase=EOD_DEEP_DIVE
只对shortlist做K线、技术、筹码、资金、F10/财务、板块和最终候选新闻核验。
生成次日A/B/REJECT分级、触发条件、失效条件和风险说明；可更新watchlist，但watchlist不是交易授权。
```

## 9. NIGHT_EVENT_CHECK

```text
phase=NIGHT_EVENT_CHECK
只检查现有持仓和A级候选的公告/重大事件风险。
可以降级/阻断候选，不因单条非结构化消息升级买入等级。禁止下单。
```

## 10. RECOVERY_SYNC

```text
phase=RECOVERY_SYNC
这是恢复任务，不做选股。重新获取交易阶段、余额、持仓、挂单、当日成交、PnL和已注册intent，并与本地状态对账。
只有一致性恢复后才能进入当前正常phase；否则DEGRADED_NO_ORDER。
```


## 11. DAILY_REVIEW

```text
phase=DAILY_REVIEW
汇总今日所有 signal/risk/order/trade/PnL、market/sector context 与异常日志。
生成符合 review_report.schema.json 的 REVIEW_REPORT，并可生成 change_proposal.schema.json 的改进假设。
严禁修改生产参数；所有 proposal 的 production_effect=false。
```

## v5 AUDIT_FINALIZE
每天 DAILY_REVIEW 完成后运行，只做本地日志归档，不调用交易写接口。

任务：
1. 校验当日事件 hash chain；
2. 检测 signal→risk→intent→order→receipt→review 是否存在审计缺口；
3. 生成 `daily_manifest.schema.json`；
4. 将 review_id、PnL、候选漏斗和版本集合写入 manifest；
5. 如果 hash chain 或关键链路不完整，产生 SYSTEM_ERROR/AUDIT_GAP，不得静默忽略。
