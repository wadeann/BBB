# Backtest Data Quality & Bias Policy

## 目的

回测结果只有在数据时点和覆盖率可解释时才有研究价值。任何数据不足都必须显式写入 `report.json`，不能静默忽略。

## 质量等级

### A — Research Grade

必须满足：

- Point-in-Time historical universe
- 无明显幸存者偏差
- 历史价格覆盖率 >= 99%
- Universe 覆盖率 >= 98%
- 无已知 look-ahead
- 财务/公告若参与，必须有 publish_time/available_at
- 板块历史归属至少达到设定覆盖阈值

### B — Diagnostic Grade

可用于：

- 程序验证
- MCP 字段验证
- 性能测试
- 初步策略方向研究

可能存在：

- 当前股票池回测历史
- 当前 sector mapping
- 部分缺失股票

报告必须：

```json
{
  "research_grade": false
}
```

### C — Invalid for Strategy Claims

出现以下任何一种：

- 使用未来价格参与当日决策
- 信号和成交发生在同一收盘价且无可成交依据
- 历史股票池明显缺失且未披露
- 使用未来财报/公告
- 数据缺口被大量静默跳过

这种结果不能用于声称策略有效。

## 建议报告字段

```json
{
  "data_quality": {
    "grade": "A",
    "research_grade": true,
    "point_in_time_universe": true,
    "universe_coverage": 0.995,
    "price_coverage": 0.999,
    "sector_membership_point_in_time": true,
    "sector_coverage": 0.94,
    "survivorship_bias": false,
    "lookahead_detected": false,
    "dataset_versions": {},
    "warnings": []
  }
}
```

## 动态股票池规则

每天重新获取/重建 Universe。

不要把一个固定 2026 股票列表用于 2024~2026 全周期。

## 历史板块规则

优先使用历史有效期数据；如果只能使用当前映射，必须标记。

## 缺失数据规则

每次缺失都计数：

```text
NO_BARS
NO_SECURITY_STATUS
NO_SECTOR_HISTORY
PARTIAL_UNIVERSE
MISSING_TRADING_DAY
```

最终报告应显示按原因统计。

## 可复现性

建议记录：

- code version
- config hash
- strategy version
- dataset version
- universe hash per date
- backtest settings
- run id

相同输入应得到相同输出。

---

## v0.6 Research Validity 自动分级

v0.6 每个 Research Suite 实验增加：

```json
{
  "research_validity": {
    "grade": "RESEARCH_GRADE | DIAGNOSTIC_ONLY",
    "reasons": [],
    "tested_symbols": 0,
    "neutral_sector_trade_share": 0.0,
    "survivorship_bias": false
  }
}
```

默认会因以下原因降级：

- 股票覆盖低于 `config/research.yaml:min_symbols_for_research_grade`；
- 股票池存在幸存者偏差；
- 历史行业/板块 membership 不是 Point-in-Time；
- 交易几乎都落在 `NEUTRAL_SECTOR`；
- 历史板块行情缺失。

## 本地 Point-in-Time Security Master

如果 Intel MCP 暂时没有历史股票池接口，可以放：

```text
data/backtest/security_master.csv
```

模板见：

```text
data/backtest/security_master.example.csv
```

支持字段：

```text
symbol
active_from
active_to
tradable
st
suspended
board
```

同一个 symbol 可以有多行状态区间。正式数据应把 ST、停牌、退市整理等历史状态拆成有效区间，而不是只保存今天状态。

## LLM 历史决策的数据泄漏风险

v0.6 默认匿名化 ticker，且禁止历史 LLM Gate 调用实时数据源。这能降低但不能完全消除模型训练语料带来的历史知识污染，因此：

- LLM A/B 必须单独标记；
- 不应把 LLM 回测结果与纯确定性回测混成一个未经说明的数字；
- 报告 `methodology.llm_filter_stats` 会保留匿名化、调用次数和缓存信息。
