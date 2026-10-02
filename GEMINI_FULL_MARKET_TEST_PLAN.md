# Gemini 接力测试入口 — v0.7 Full Market

> Gemini/其他 LLM 接手后，请先读完本文件、`FULL_MARKET_BACKTEST.md`、`MCP_HISTORICAL_DATA_CONTRACT.md`。不要先改仓位、止损或策略阈值。

## Phase 0 — 安装与代码回归

```bash
cd /opt/a-share-agent
python3 -m venv .venv
.venv/bin/pip install -e .
PYTHONPATH=. .venv/bin/pytest -q
```

必须全部 PASS。

## Phase 1 — MCP 历史能力检查

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production mcp-probe --service intel
```

确认 optional historical tools 是否包含：

```text
mcp_intel_get_historical_universe
mcp_intel_get_historical_sector_membership
```

如果缺少，先按 `MCP_HISTORICAL_DATA_CONTRACT.md` 完善 Intel MCP，不要用63只静态池代替正式验证。

## Phase 2 — Full Market Research Preflight

先做三个月：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" --backend production \
  research-preflight \
  --start 2026-07-01 \
  --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --sample-size 60 \
  --require-research-grade
```

报告：

```text
data/diagnostics/latest_research_preflight.json
```

必须：

```text
formal_full_market_ready = true
survivorship_bias = false
point_in_time = true
dynamic_daily = true
每日 active universe >= research_grade_min
sector_mapping_coverage >= 0.80
sector_history_asof_coverage >= 0.80
```

失败：停止正式收益实验，先修数据。

## Phase 3 — 50只冒烟测试（不解释收益）

```bash
.venv/bin/a-share-agent \
  --root "$PWD" --backend production \
  research-suite \
  --start 2026-07-01 --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --max-universe 50 \
  --experiment baseline \
  --experiment no_triple_golden_cross
```

目的只有：验证接口、缓存、报告和耗时。

## Phase 4 — 500只性能测试（仍不做最终盈利结论）

同上，把 `--max-universe 500`。

记录：

- 首次K线缓存耗时；
- 第二次复跑耗时；
- MCP 错误数；
- 缺失K线数；
- 内存峰值。

## Phase 5 — 三个月全市场 deterministic A/B

正式运行时 **不要设置 max-universe**：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" --backend production \
  research-suite \
  --start 2026-07-01 --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --experiment baseline \
  --experiment no_triple_golden_cross \
  --experiment core_signal_focus \
  --experiment router_disabled \
  --experiment sector_disabled
```

先看：

- `research_validity.grade`
- `active_universe_min/avg/max`
- `point_in_time_universe_all_days`
- historical sector coverage

只有 `RESEARCH_GRADE` 才解释收益。

## Phase 6 — 三个月全市场 LLM A/B

先：

```bash
.venv/bin/a-share-agent --root "$PWD" llm-probe
```

再：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" --backend production \
  research-suite \
  --start 2026-07-01 --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --include-llm \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

LLM A/B 解释前必须：

```text
llm_filter_stats.failures == 0
llm_filter_stats.error_candidates == 0
research_validity.llm_experiment_valid == true
```

## Phase 7 — 两年全市场

前三个月通过以后才跑：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" --backend production \
  research-suite \
  --start 2024-10-01 --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --experiment baseline \
  --experiment no_triple_golden_cross \
  --experiment core_signal_focus \
  --experiment router_disabled \
  --experiment sector_disabled
```

LLM 两年测试可后置，先让 deterministic 全市场结果稳定。

## Phase 8 — Walk-Forward

只有两年 full-market deterministic `RESEARCH_GRADE` 后再做参数 Walk-Forward；不得在同一两年样本上反复调参后仍称其为样本外验证。

## 报告在哪里

Preflight：

```text
data/diagnostics/latest_research_preflight.json
```

单次回测：

```text
data/backtest/runs/<bt-id>/report.json
```

Research Suite：

```text
data/research/runs/<suite-id>/
  research_summary.json
  experiment_metrics.csv
  feedback_bundle.zip
```

## 反馈给 ChatGPT 的文件

优先上传：

1. `latest_research_preflight.json`
2. `feedback_bundle.zip`

若是两年正式研究，再加：

3. 最优/基线实验的 `report.json`
4. `trades.csv`
5. `rejections.csv`

## 报告必须区分

### Supported
只写数据直接支持的结论。

### Not Supported
必须明确：

- 不能由短窗口推导长期盈利；
- 不能由静态/截断股票池推导全市场；
- 不能因 LLM 少交易就认定产生 Alpha；
- 不能在 Expectancy<=0 时通过扩大仓位“解决”策略问题。
