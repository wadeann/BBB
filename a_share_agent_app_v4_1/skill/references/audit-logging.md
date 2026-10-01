# 审计日志与交易回放规范 v5

## 1. 目标
日志系统必须能够回答以下问题：
1. 某个交易日系统看到了什么市场数据？
2. 当天全市场初筛出了哪些股票，哪些被淘汰，为什么？
3. 某只股票为什么进入/没有进入候选池？
4. 当时使用了哪个策略版本、配置、Prompt、模型和代码版本？
5. Risk 为什么 PASS/REJECT？
6. 最终为什么下单、没有下单、撤单或未成交？
7. 实际成交价格与计划价格相差多少？
8. 当晚复盘如何归因，提出了哪些改进假设？
9. 能否用当天保存的数据完整重建决策链，而不访问未来数据？

## 2. 设计原则
- **Append-only**：生产日志只能追加，不能覆盖或删除历史事件。
- **Event sourced**：订单、持仓、候选、复盘状态均能由事件重建。
- **Tamper evident**：事件使用 SHA-256 hash chain，修改历史记录会破坏链。
- **Raw + Derived 分离**：MCP原始回执与LLM派生结论分别保存。
- **版本化**：所有决策绑定 strategy/config/prompt/model/code/schema 版本。
- **可重放**：保存数据快照或内容哈希，支持 exact replay。
- **最小敏感信息**：不记录Token、密码、Cookie、券商密钥；账户标识哈希化。

## 3. 推荐持久化结构
```text
data/audit/
  audit_index.sqlite
  2026/
    10/
      08/
        events.jsonl
        manifest.json
        snapshots/
          <sha256>.json.gz
        prompts/
          <message_id>.json.gz
        reviews/
          <review_id>.json
```

`events.jsonl` 是当日审计真相源；SQLite 只是查询索引，可随时由 JSONL 重建。

## 4. 必须记录的事件
### 系统与调度
- `RUN_STARTED`
- `RUN_COMPLETED`
- `PHASE_CHANGED`
- `SYSTEM_ERROR`
- `DATA_SOURCE_HEALTH`

### 市场/板块
- `MARKET_CONTEXT`
- `SECTOR_CONTEXT`
- `STRATEGY_ROUTE`

### 选股
- `UNIVERSE_SCAN_STARTED`
- `UNIVERSE_SCREEN_RECORD`
- `CANDIDATE_BATCH`
- `DEEP_DIVE_REPORT`
- `SIGNAL_DECISION`
- `CANDIDATE_REJECTED`

### 风控/交易
- `PORTFOLIO_SNAPSHOT`
- `RISK_INTENT`
- `RISK_RESULT`
- `INTENT_REGISTERED`
- `ORDER_REQUEST`
- `EXECUTION_RECEIPT`
- `ORDER_CANCELLED`
- `POSITION_SNAPSHOT`
- `PNL_SNAPSHOT`

### 复盘与学习
- `DAILY_REVIEW`
- `CHANGE_PROPOSAL`
- `VERSION_PROMOTED`
- `VERSION_ROLLED_BACK`

## 5. Event Envelope
每条事件都必须符合 `schemas/audit_event.schema.json`。
关键字段：
- `event_id`
- `event_type`
- `event_time`
- `trade_date`
- `phase`
- `run_id`
- `trace_id`
- `message_id`
- `parent_event_id`
- `symbol`
- `strategy_id`
- `strategy_version`
- `config_hash`
- `prompt_hash`
- `model_id`
- `code_version`
- `schema_version`
- `source_refs`
- `payload_ref` 或 `payload`
- `previous_event_hash`
- `event_hash`

## 6. 全市场选股日志的粒度
为了既能回溯，又不把5000只股票的完整深度数据全部保存：

### 全股票池
每只股票至少保存一条紧凑 `UNIVERSE_SCREEN_RECORD`：
- symbol/name；
- hard_filter_pass；
- filter_reason_codes；
- strategy_family_candidates；
- compact_factors；
- score_preliminary；
- market/sector gate；
- source snapshot hash。

### 进入深度分析的股票
额外保存：
- 完整K线/技术因子快照引用；
- 筹码/资金/F10/新闻引用；
- `DEEP_DIVE_REPORT`；
- `SIGNAL_DECISION`；
- `reasons_for` / `reasons_against`；
- 最终 score 与阈值。

这样可回答“为什么A入选、B被淘汰”。

## 7. 订单生命周期必须完整
同一订单链必须通过 `trace_id + intent_id` 串联：
```text
SIGNAL_DECISION
 -> RISK_INTENT
 -> RISK_RESULT
 -> INTENT_REGISTERED
 -> ORDER_REQUEST
 -> EXECUTION_RECEIPT
 -> POSITION_SNAPSHOT
 -> DAILY_REVIEW
```
任何缺口都记为 `AUDIT_GAP`，并进入当日系统健康复盘。

## 8. 每日 Manifest
每天收盘后生成 `manifest.json`，符合 `schemas/daily_manifest.schema.json`，至少包含：
- trade_date；
- market regime/主线摘要；
- universe数量；
- hard filter通过数；
- deep-dive数量；
- entry/watch/reject数量；
- risk PASS/REJECT数量；
- orders/fills/cancels；
- realized/unrealized PnL；
- review_id；
- event_count；
- first/last event hash；
- strategy/config/prompt/model/code版本集合；
- data_quality/系统异常摘要。

## 9. 两种回放
### Exact Replay
目标：解释“当时为什么这么做”。
- 使用当时保存的原始快照；
- 使用当时生产LLM的已保存输出；
- 不重新访问今天的数据；
- 不允许下单。

### Counterfactual Replay
目标：研究“如果当时使用新策略/新模型会怎样”。
- 输入仍使用当时快照；
- 可以换 strategy/config/model；
- 输出标记 `counterfactual=true`；
- 永远不可触发真实/模拟下单接口；
- 与 production result 并排比较。

## 10. 日志查询要求
运行时至少提供以下查询能力：
- `get_day_timeline(trade_date)`
- `get_symbol_trace(trade_date, symbol)`
- `get_order_trace(intent_id)`
- `get_candidate_funnel(trade_date)`
- `get_review(trade_date)`
- `get_change_proposals(date_range)`
- `verify_hash_chain(trade_date)`
- `replay_day(trade_date, mode="exact")`

这些可以先由本地 Python + SQLite 实现，不要求新的MCP。

## 11. 每晚复盘必须引用日志ID
`DAILY_REVIEW` 中的每个主要结论必须引用至少一个：
- event_id；
- trace_id；
- snapshot hash；
- intent_id。

避免复盘变成不可验证的自然语言故事。

## 12. 数据保存与容量
建议：
- 订单/成交/复盘/决策日志：至少10年；
- Prompt/Response：至少5年或按合规需求；
- 原始行情快照：至少2年，长期数据可以归档压缩；
- 系统健康debug日志：6个月。

若空间不足，优先保留：成交链 > 候选决策 > 复盘 > 全市场紧凑筛选 > 原始大体量快照。

## 13. 安全
绝不记录：API key、Token、密码、Cookie、券商密钥。
错误日志中的请求头、URL参数、异常堆栈也必须先做脱敏。
