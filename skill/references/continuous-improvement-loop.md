# 受控持续改进闭环 v4

## 1. 目标
闭环的目标不是让 LLM 每天“自动变聪明”，而是让系统每天积累可审计证据，并把改进从主观感受变成可验证实验。

## 2. 两个闭环

### A. 运营闭环（当天即可作用）
修复：数据源异常、重复单、状态不一致、订单错误、freshness、调度故障。
这些属于系统可靠性，可以在确认原因后修复代码/配置，但仍需版本记录。

### B. 策略学习闭环（不得当天自动上线）
优化：信号阈值、策略权重、market/sector router、仓位、退出逻辑。
流程必须是：

`Observe → Attribute → Hypothesis → Backtest → Walk-forward/OOS → Paper Shadow → Approval → Promote → Monitor → Rollback`

## 3. DAILY_REVIEW 输入
- 今日所有 SignalDecision；
- 所有 RiskResult；
- 所有 OrderRequest / ExecutionReceipt；
- 收盘余额、持仓、PnL；
- 当日 market/sector context 序列；
- 异常/超时/数据冲突日志；
- 未交易候选与 gate 阻断记录。

## 4. DAILY_REVIEW 输出

### ReviewReport
至少包含：
- 当日净收益与风险暴露；
- 按 strategy_family 分解；
- 按 market_regime/sector_strength 分解；
- MFE/MAE；
- 实际滑点与计划滑点；
- 被 Risk 正确阻止的订单；
- 错失机会（counterfactual，仅研究用途）；
- error taxonomy；
- 数据/模型/执行健康；
- 明日需关注但**不自动生效**的改进假设。

### Error taxonomy
- `SELECTION_ERROR`：选错标的；
- `REGIME_ROUTING_ERROR`：策略与环境不匹配；
- `ENTRY_TIMING_ERROR`；
- `EXIT_TIMING_ERROR`；
- `POSITION_SIZING_ERROR`；
- `EXECUTION_ERROR`；
- `DATA_ERROR`；
- `RULE_AMBIGUITY`；
- `NO_ERROR/EXPECTED_VARIANCE`。

注意：亏损交易不自动等于“策略错误”，盈利交易也不自动等于“决策正确”。

## 5. 关键指标
除了 PnL，至少追踪：
- expectancy/trade；
- profit factor；
- payoff ratio；
- max drawdown；
- MFE / MAE；
- fill rate；
- slippage bps；
- turnover；
- exposure；
- 被 gate 阻断交易的反事实表现；
- 按 regime 的策略稳定性。

## 6. 为什么不能每天自动调参
每天按最近几笔交易调权重会产生：
- 过拟合；
- 追涨杀跌式参数漂移；
- 无法分辨随机波动与真实失效；
- 回测与生产策略不一致；
- 无法审计。

因此 DAILY_REVIEW 只产生 hypothesis。

## 7. ChangeProposal 示例

```json
{
  "proposal_id":"CP-20261008-001",
  "target":"strategy_router.trend_breakout.neutral_market",
  "change":"candidate_threshold_delta: 5 -> 8",
  "reason":"最近样本显示 neutral 市场突破策略 MAE 上升",
  "evidence_scope":{"trades":42,"days":35},
  "status":"hypothesis",
  "required_tests":["historical_backtest","walk_forward","paper_shadow"],
  "production_effect":false
}
```

## 8. 晋级流程
1. 历史回测：必须使用当时可得数据，禁止未来函数；
2. Walk-forward / OOS：验证不是样本内拟合；
3. 与当前 production baseline 对比；
4. 分 regime 分析，避免总体好但关键环境崩溃；
5. Paper shadow：新旧策略并行，仅旧版执行；
6. 人工审批；
7. 新版本发布，生成新 version/hash；
8. 监控；若明显恶化可 rollback。

## 9. 月收益目标的处理
可报告“距离研究目标30%还有多少”，但禁止生成：
- 为追赶月目标增加 risk_per_trade；
- 为完成目标降低 candidate threshold；
- 亏损后加倍下注；
- 因月末临近而强制交易。

生产系统只执行有正期望且满足风险约束的交易；没有机会时 `NO_TRADE` 是合格输出。
