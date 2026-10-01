# A-share Signal Engine Skill v5

v5 在 v4 的“行情路由 + Handoff Contract + Controlled Learning Loop”基础上，新增**完整审计日志与交易回放规范**。v4 的三个生产级模块仍保留：

1. **Regime Strategy Router**：不同大盘、情绪、板块强弱/生命周期选择不同策略族；
2. **Handoff Contract**：每个 LLM/MCP/风控/执行步骤使用可验证 JSON envelope，不再靠自然语言交接；
3. **Controlled Learning Loop**：每日复盘、归因、提出改进假设，但禁止 LLM 日内自动改生产参数。

## 关于“月收益30%”

可作为研究目标或压力测试，不应成为强制交易 KPI。生产系统不得为了追赶月收益目标降低门槛、加仓、追涨或覆盖风控。评价体系必须同时包含回撤、期望值、盈亏比、滑点、稳定性与不同 regime 下的表现。

## 新增文件

- `config/strategy_router.yaml`：行情/板块 → 策略族路由
- `config/improvement.yaml`：受控学习与晋级规则
- `references/regime-strategy-routing.md`：路由设计说明
- `references/handoff-contracts.md`：上下游 JSON 交接契约
- `references/continuous-improvement-loop.md`：每日复盘与版本晋级闭环
- `schemas/handoff_envelope.schema.json`
- `schemas/strategy_route.schema.json`
- `schemas/review_report.schema.json`
- `schemas/change_proposal.schema.json`

## DAILY_REVIEW
默认在 `21:00–21:30 Asia/Shanghai` 运行，只读今日信号、风控、订单、成交、PnL 与上下文，生成复盘和改进假设。它没有下单权，也没有生产参数修改权。

## 推荐完整闭环

`市场/板块状态 → 策略路由 → 候选 → 深挖 → 信号 → 风控 → 意图 → 执行 → 成交 → 日复盘 → 改进假设 → 回测/WF/OOS → paper shadow → 审批 → 新版本 → 监控/回滚`

## 部署前仍需完成
- 对 strategy_router 的规则做历史回测与 walk-forward；
- 让 scheduler/execution 层真正校验 handoff schema 与 freshness；
- Runtime 按 v5 `audit-logging.md` 实现 event writer / SQLite index / snapshot store / replay API；
- 建 change proposal 的离线回测流水线；
- 对 Risk/Exec/数据源进行故障注入与幂等测试；
- 若要真实资金，新增明确的真实券商执行适配器。


## v5 新增：完整审计日志与交易回放

v5 在 v4 的 trace/run/intent 基础上增加正式的事件溯源日志规范。每个交易日可回溯从大盘/板块环境、全市场初筛、候选淘汰、深度分析、信号、风控、订单、成交直到 DAILY_REVIEW 的完整链路。

核心文件：
- `references/audit-logging.md`：日志、候选漏斗、回放与保留策略；
- `config/logging.yaml`：运行时日志配置；
- `schemas/audit_event.schema.json`：单事件契约；
- `schemas/daily_manifest.schema.json`：每日归档清单。

推荐实现为 append-only JSONL + SQLite 查询索引 + gzip 原始快照。SQLite 不是审计真相源；可以从 JSONL 重建。
