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
