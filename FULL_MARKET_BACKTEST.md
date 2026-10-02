# v0.7 Full-Market Point-in-Time Backtest

## 目标

正式回测不再使用固定 `universe.txt` 作为两年股票池。每个历史交易日都必须重建当时可交易的全A股集合，然后在该集合上执行：

```text
当日历史全A股
→ ST/风险警示/退市整理/停牌/不可交易过滤
→ 历史行业归属 + 行业历史走势
→ Market Regime + Sector Router
→ 技术信号
→ Score/Router
→ 可选 LLM Gate
→ Risk
→ 次日开盘模拟成交
```

这与未来实盘的“每天重新扫全市场”保持同一方向。

## 正式研究硬条件

正式收益结论必须同时满足：

1. `--universe-mode strict_point_in_time`
2. `max_universe=0`（不截断股票池）
3. 每个历史交易日 `point_in_time=true`
4. 无 current-universe silent fallback
5. 股票池无已知幸存者偏差
6. 历史行业映射覆盖率 >= 80%
7. 历史价格抽样覆盖率 >= 90%
8. LLM 实验：`failures=0` 且 `error_candidates=0`

否则报告只能标记为 `DIAGNOSTIC_ONLY`。

## 历史 Universe 数据路径

Runtime 按以下顺序选择：

### 1. 本地 security_master.csv

`data/backtest/security_master.csv`

支持有效期区间；适合离线研究。

### 2. Intel MCP interval fast path

优先调用：

```text
mcp_intel_get_historical_universe(
  start_date,
  end_date,
  mode="membership_intervals",
  include_status=true
)
```

如果返回完整有效期区间，一次取回周期内所有历史成员关系，是两年全市场回测的推荐方式。

### 3. Intel MCP daily PIT path

如果 interval 模式不存在，则按每个历史交易日分页：

```text
mcp_intel_get_historical_universe(
  date="YYYY-MM-DD",
  market="A_SHARE",
  page=1..N,
  limit=500
)
```

返回会缓存到：

```text
data/backtest/cache/daily_universe/
```

后续重复实验不需要重复拉取。

## 历史行业

优先级：

1. historical universe 当天直接返回 `industry_code/industry_name`
2. security master 历史有效期字段
3. `mcp_intel_get_historical_sector_membership(symbol, as_of)`
4. 只有 diagnostic 模式才允许 current F10 fallback

若历史 sector MCP 返回 `effective_from/effective_to`，Runtime 会按 symbol 缓存有效区间，避免 5000股票 × 500天逐日调用。

## 报告新增字段

`report.json`：

```json
{
  "coverage": {
    "dynamic_universe_days": 0,
    "active_universe_min": 0,
    "active_universe_avg": 0,
    "active_universe_max": 0,
    "tested_symbols": 0
  },
  "data_quality": {
    "point_in_time_universe_all_days": true,
    "dynamic_universe_daily": true,
    "sector_mapping_coverage": 0.0,
    "historical_sector_mapping_coverage": 0.0
  },
  "universe_daily": [
    {
      "date": "2025-01-02",
      "active_symbols": 5300,
      "universe_hash": "...",
      "point_in_time": true,
      "dataset_version": "..."
    }
  ]
}
```

每天记录 universe hash，便于回放和数据版本审计。

## 性能建议

首次两年全市场运行会慢，因为需要为数千只股票建立历史K线缓存。推荐：

1. 先 `--max-universe 50` 冒烟测试；
2. 再 500 只性能测试；
3. 最后正式 `--max-universe 0`；
4. 不删除 `data/backtest/cache/bars/`，后续消融实验直接复用。

`max_universe > 0` 的结果只能用于测试，不用于策略收益结论。
