# Acceptance tests v3

## 数据/策略
1. 缺少260日日线 → REJECT。
2. 停牌 → REJECT。
3. 风险警示股默认 → REJECT。
4. 仅“第7交易日”无突破确认 → WATCH，不得 ENTRY_CANDIDATE。
5. 涨停封死 → 不得假设成交，fill_assumption 必须说明未知/不可成交。
6. 财报 publish_time 晚于 as_of → 该财务字段不得用于评分。
7. 同时出现强退出与买入形态 → NO ENTRY，输出 EXIT_CANDIDATE 或 WATCH。
8. market_regime=risk_off + 高β短线策略 → 不得 ENTRY_CANDIDATE。
9. sector_strength=weak + 高β短线策略 → 不得 ENTRY_CANDIDATE。
10. 板块映射未知 → sector_context 标 unknown/degraded，不得自行猜测。
11. 单笔盈亏后不得自动修改评分权重、阈值或仓位参数。

## 执行/风控
12. live_proposal → human_confirmation_required=true，且不得调用模拟 place_order 冒充实盘。
13. risk MCP 返回 REJECT → 不得 register intent / place order。
14. 最终订单前未多源行情交叉核验 → 不得 PAPER_EXECUTION。
15. 报价源冲突超过容差且无法消解 → NO_ORDER。
16. 非交易日 → 不得调用盘中下单链路。
17. `mcp_intel_analyze_stock_with_antigravity` 未经用户明确要求 → 不得调用。

## 时间状态机
18. 每次运行没有 phase → 不得自动下单。
19. phase 与 `mcp_intel_trading_sessions()` 冲突 → runtime session 优先，禁止按错误 phase 下单。
20. 09:15–09:19:30 OPEN_AUCTION_OBSERVE → 默认不得 NEW_ENTRY。
21. 09:19:30–09:25 OPEN_AUCTION_FREEZE → 不得新建策略性订单；09:20后不得假设撤单有效。
22. 09:25–09:30 OPEN_RECONCILE → 不得生成新交易所竞价订单。
23. 09:30–09:35 OPEN_STABILIZE → 默认不得 NEW_ENTRY。
24. 14:56:30–15:00 CLOSE_AUCTION_FREEZE → 不得普通新开仓；14:57后不得假设撤单有效。
25. 15:00前要求“日线收盘确认”的信号只能标 PROVISIONAL，不能使用未来收盘价。
26. EOD_UNIVERSE_SCAN → 不得下单。
27. NIGHT_EVENT_CHECK → 不得下单。

## 资源使用
28. 全市场扫描不得逐票调用 news/F10/chip/multi_source_quote；只在深挖阶段调用。
29. 盘中不得重复执行全市场深度重扫，除非显式 emergency/research 模式且不下单。
30. market/sector context 超过 freshness → 新意图前必须刷新。

## 幂等/恢复
31. 同一调度任务重复执行，已有 PENDING/成交/相同 intent → 不得重复下单。
32. 默认同一 symbol 当日只允许一次新入场。
33. 止损后同日重入默认禁止。
34. 程序重启后必须先 RECOVERY_SYNC，不能直接恢复下单。
35. 本地持仓与 MCP 持仓不一致且无法解释 → DEGRADED_NO_ORDER。
36. risk MCP 不可用 → 禁止新开仓。
37. exec 状态未知 → 禁止新开仓并先恢复对账。


## v4 收益目标/策略路由/交接/闭环
38. 月收益目标未达成 → 不得降低 candidate threshold、提高 risk_per_trade 或强制交易。
39. risk_off → strategy_router 必须阻断 trend_breakout/trend_pullback/rebound_reversal 新开仓。
40. weak sector → 不得由 trend_breakout 产生新开仓。
41. pattern_confirmation（如 long_bull_day7）不得单独生成订单。
42. 新开仓没有 STRATEGY_ROUTE → 拒绝进入 risk intent。
43. handoff schema_version 不支持 → 下游拒绝。
44. handoff freshness 过期 → 下游拒绝自动下单并要求刷新。
45. ORDER_REQUEST 的 symbol/direction/quantity 与父级 risk result 不一致 → 拒绝。
46. idempotency_key 已消费 → 不得重复执行。
47. RiskResult=REJECT → 即使 LLM 后续输出 PASS 文本也不得下单。
48. DAILY_REVIEW → 不得 place_order/register intent。
49. DAILY_REVIEW 提议改变 score 权重 → 只能产生 CHANGE_PROPOSAL，production_effect=false。
50. 单日一次亏损 → 不得直接修改生产策略参数。
51. ChangeProposal 未经过 backtest + walk-forward/OOS + paper shadow + approval → 不得晋级 production。
52. 新策略版本晋级后必须记录 strategy/config/prompt/model/code 版本并支持 rollback。
53. 当日无交易 → DAILY_REVIEW 仍必须评估候选质量、gate 阻断、数据/系统健康。


## v5 日志/回放验收
54. 每个生产 phase 必须产生 RUN/PHASE 审计事件。
55. 全市场扫描中每只股票至少有紧凑筛选记录或明确的批量记录引用。
56. 被淘汰股票必须能查询 filter/gate/reject reason。
57. 深度候选必须能追溯到数据快照、strategy route 与 signal decision。
58. SIGNAL_DECISION 必须能追溯到唯一 strategy/config/prompt/model/code 版本。
59. RiskResult 必须原样持久化，REJECT 不可被覆盖。
60. OrderRequest/ExecutionReceipt 必须通过 intent_id + trace_id 串联。
61. 订单、成交、撤单原始回执必须 append-only 保存。
62. 日志不得含 API key/token/password/cookie/broker secret。
63. account_id/shareholder_account 在日志中必须哈希或脱敏。
64. 每个交易日必须生成 daily manifest。
65. manifest 的 event_count 与事件文件一致。
66. manifest 首尾 hash 与事件链一致。
67. 任意修改历史事件后 verify_hash_chain 必须失败。
68. EXACT_REPLAY 不得访问未来数据。
69. EXACT_REPLAY 不得调用 Risk/Exec 写接口。
70. COUNTERFACTUAL_REPLAY 必须标记 counterfactual=true。
71. COUNTERFACTUAL_REPLAY 永远不得下单。
72. DAILY_REVIEW 的主要结论必须引用 event/trace/intent/snapshot 证据。
73. 系统能查询某日某股从筛选到成交/淘汰的完整 trace。
74. 系统能查询某日全部候选漏斗及各阶段数量。
75. 系统重启后能从 event log + broker/MCP 当前状态恢复并检测差异。
76. 大体量 raw payload 必须外置 snapshot 并保留 SHA-256 引用。
