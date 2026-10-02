# v0.6 Research Lab 设计说明

## 目标

v0.6 用标准化 A/B 与消融实验回答四个问题：

1. 确定性核心策略是否存在正期望；
2. 哪个策略贡献/破坏组合收益；
3. Market/Sector Strategy Router 是否真的增加价值；
4. LLM 对同一批历史候选做二次过滤后，是否有**可重复的增量价值**。

## 研究链

```text
Point-in-Time Universe
        ↓
Historical Market Regime
        ↓
Historical Sector Context
        ↓
Deterministic Signal Engine
        ↓
Deterministic Score
        ↓
Strategy Router
        ↓
(optional) Historical LLM Gate
        ↓
Risk sizing
        ↓
Next-session execution simulator
        ↓
T+1 / price-limit / costs / slippage
        ↓
Gross / Cost / Net metrics
        ↓
Research comparison report
```

## LLM Gate 不是“让模型自由炒股”

LLM Gate 只接受已经满足确定性规则的 candidate，并输出：

```json
{
  "decision": "PASS|WATCH|REJECT",
  "confidence": 0,
  "reasons_for": [],
  "reasons_against": [],
  "risk_flags": []
}
```

历史 LLM Gate 不允许调用 MCP、新闻或 Web；发生 LLM 错误时 **fail closed = REJECT**。

## 为什么默认匿名 ticker

一个现代 LLM 可能在参数记忆中知道过去两年的真实股票后续走势。若直接告诉模型 `600xxx.SH`，即使 Prompt 要求“只看当时数据”，仍存在隐性未来知识污染。

因此默认：

```yaml
llm_filter_anonymize_symbol: true
```

模型只看到稳定匿名ID。真实 ticker 只用于缓存 key 和最终研究报告，不进入模型输入。

## Point-in-Time 数据

v0.6 支持：

1. `data/backtest/security_master.csv` 本地历史证券主表；
2. 可选 MCP `mcp_intel_get_historical_universe`；
3. 若两者都没有，在 `prefer_point_in_time` 下才降级到当前股票池，并明确标记幸存者偏差；
4. `strict_point_in_time` 下不允许降级。

本地 `security_master.csv` 至少支持：

```csv
symbol,active_from,active_to,tradable,st,suspended
600001.SH,2024-01-01,2025-06-30,1,0,0
600002.SH,2025-02-10,,1,0,0
```

这解决“今天的股票池回测过去”的基础幸存者偏差问题。

## 历史行业/板块

当前代码能够用历史板块价格计算 sector strength，但如果股票到板块的映射来自当前 F10，仍然不是严格 Point-in-Time。

因此 v0.6 报告增加：

```text
historical_sector_membership_point_in_time
sector_mapping_sources
```

若不是严格历史映射，Research Validity 会降级。

## 不自动修改生产策略

即使某个实验两年收益更高，也不能自动写回生产配置。

正确流程：

```text
Research Suite
→ Walk-Forward / OOS
→ Paper Shadow
→ Change Proposal
→ 人工审核
→ 新版本
```
