# LLM 上下游交接协议 v4

## 1. 总原则
自然语言只用于解释，**机器执行只认结构化消息**。每一步输出都必须包装在 `handoff_envelope` 中，并通过 JSON Schema 验证。

## 2. 通用 Envelope

```json
{
  "schema_version": "4.0.0",
  "message_type": "SIGNAL_DECISION",
  "message_id": "uuid",
  "parent_message_id": "uuid-or-null",
  "run_id": "20261008-ENTRY_WINDOW_AM-001",
  "trace_id": "uuid",
  "idempotency_key": "stable-key",
  "phase": "ENTRY_WINDOW_AM",
  "as_of": "2026-10-08T09:42:00+08:00",
  "data_cutoff": "2026-10-08T09:41:55+08:00",
  "freshness_seconds": 30,
  "producer": {"type":"llm","id":"model-or-service"},
  "payload": {},
  "data_quality": {"state":"ok","missing":[],"conflicts":[]},
  "source_refs": [],
  "errors": [],
  "next_action": "RISK_CHECK"
}
```

## 3. 关键 message_type

### PRECHECK_RESULT
上游：交易日/数据源/账户同步。
下游：PREOPEN_CONTEXT。
硬条件：`tradable=true` 且账户/订单状态一致，否则不得进入自动下单链。

### MARKET_CONTEXT
上游：基准指数、market_health、limitup_ladder、mainline_lanes。
下游：STRATEGY_ROUTE / SIGNAL_DECISION。
必须包含 freshness 和 data_quality。

### STRATEGY_ROUTE
上游：MARKET_CONTEXT + sector_context。
下游：策略扫描/评分。
必须声明 allow/block/conditional，不允许下游自行新增策略族。

### CANDIDATE_BATCH
上游：EOD_UNIVERSE_SCAN。
下游：EOD_DEEP_DIVE。
只包含初筛候选、过滤原因、批量因子，不包含未经验证的深度结论。

### DEEP_DIVE_REPORT
上游：K线/技术/筹码/资金/F10/新闻。
下游：SIGNAL_DECISION。
每项结论必须有 evidence/source_ref；无法确认则 unknown。

### SIGNAL_DECISION
上游：route + deep dive + portfolio snapshot。
下游：RISK_INTENT。
必须能唯一追溯到 strategy_id、signal_version 和证据。

### RISK_INTENT
上游：SIGNAL_DECISION。
下游：Risk MCP。
数量必须是上限而不是“尽量买满”；包含 stop、risk_budget、position_after。

### RISK_RESULT
上游：Risk MCP。
下游：ORDER_REQUEST。
`REJECT` 是终局，不允许 LLM 改成 PASS。

### ORDER_REQUEST
上游：PASS risk result + 最新多源报价。
下游：Exec MCP。
必须绑定唯一 `intent_id` 与 `idempotency_key`。

### EXECUTION_RECEIPT
上游：Exec MCP。
下游：状态库/持仓监控/复盘。
成交、拒单、撤单都必须保留原始回执；LLM不得改写。

### REVIEW_REPORT
上游：当日所有 signal/risk/order/trade/PnL/context。
下游：CHANGE_PROPOSAL 或仅归档。
不得直接修改生产配置。

### CHANGE_PROPOSAL
上游：Review。
下游：离线研究/回测流水线。
状态只能是 `hypothesis/testing/paper_shadow/approved/rejected/rolled_back`。

## 4. 下游拒绝规则
下游必须拒绝：
1. schema 不支持；
2. phase 不允许；
3. 过期；
4. idempotency 已消费；
5. symbol/direction/quantity 与父消息不一致；
6. parent_message_id 无法找到；
7. data_quality 为 degraded 且此动作要求高质量数据；
8. risk 未 PASS；
9. ORDER_REQUEST 没有最新报价核验；
10. 生产消息引用未晋级策略版本。

## 5. 数据不可变原则
- 原始行情、账户、Risk MCP、Exec MCP 回执写入 immutable event log。
- LLM 只能新增 derived fields；不能覆盖 upstream raw fields。
- 修正错误必须产生新 message_id，并通过 `supersedes_message_id` 指向旧消息。

## 6. 版本与追踪
所有执行链必须可重放：
`market_data → route → signal → risk → intent → order → fill → review`。

因此建议持久化：
`trace_id + run_id + message_id + strategy_version + config_hash + prompt_hash + model_id + code_version`。
