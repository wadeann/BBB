# v0.6.1 Research Lab — 部署、回测与反馈测试手册

本文件是 **v0.6.1 的标准测试流程**。目标是让不同机器、不同 LLM、不同数据源跑出来的结果能够被比较，而不是只看某一次“收益率”。

> 研究原则：任何报告如果被标记为 `DIAGNOSTIC_ONLY`，只能用于排查和消融，不应当作为“策略已证明赚钱”的依据。

> **Gemini/其他 LLM 接力测试请优先阅读：`GEMINI_CONTINUATION_TEST_PLAN.md`。**
> v0.6.1 的修复原因与旧实验为何无效，见 `CHANGELOG_V0.6.1.md` 和 `AUDIT_REVIEW_GEMINI.md`。
> 一键三个月标准复测脚本：`bash scripts/run_gemini_validation.sh`。

## 0. 输出目录先记住

部署/数据自检：

```text
data/diagnostics/latest_research_preflight.json
```

单次回测：

```text
data/backtest/runs/<bt-run-id>/
├── report.json
├── report.html
├── trades.csv
├── equity_curve.csv
├── monthly_returns.csv
├── rejections.csv
├── strategy_breakdown.csv
├── family_breakdown.csv
├── route_breakdown.csv
├── sector_breakdown.csv
├── data_quality.json
└── methodology.json
```

标准 A/B Research Suite：

```text
data/research/runs/<research-suite-id>/
├── research_summary.json
├── experiment_metrics.csv
├── FEEDBACK_README.md
└── feedback_bundle.zip        # 最推荐直接反馈这个文件
```

最近一次套件指针：

```text
data/research/runs/latest.json
```

LLM 历史 Gate 缓存：

```text
data/backtest/llm_cache/
```

普通运行日志：

```text
data/logs/
```

## 1. 安装

```bash
cd /opt/a-share-agent
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

检查版本：

```bash
.venv/bin/python -c 'import a_share_agent; print(a_share_agent.__version__)'
```

预期：

```text
0.6.1
```

## 2. 配置 `.env`

项目根目录直接使用：

```text
.env
```

MCP 至少需要 Intel：

```text
ASHARE_MCP_USERNAME=...
ASHARE_MCP_PASSWORD=...
```

只有做 LLM A/B 时才需要：

```text
ASHARE_LLM_BASE_URL=...
ASHARE_LLM_API_KEY=...
ASHARE_LLM_MODEL=...
```

不要把填写真实凭证后的 `.env` 发给任何人。

## 3. 先验证 Intel MCP

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  mcp-probe --service intel
```

重点查看：

- `ok=true`
- `missing_tools`
- `optional_historical_tools`

v0.6 识别以下**可选**历史接口；它们不属于原始 53 工具，所以缺少不会让普通 MCP probe 失败：

```text
mcp_intel_get_historical_universe
mcp_intel_get_historical_security
mcp_intel_get_historical_sector_membership
```

如果服务端以后增加这些工具，v0.6 会自动允许调用。

## 4. 回测数据预检（必须先跑）

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-preflight \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --sample-size 30
```

自动写入：

```text
data/diagnostics/latest_research_preflight.json
```

### 重点看这些字段

```text
universe.symbols
universe.point_in_time
universe.survivorship_bias
sample.price_period_coverage
sample.sector_mapping_coverage
sample.sector_history_period_coverage
benchmark.covers_period
provider_warnings
research_grade_candidate
```

### 推荐研究级条件

理想状态：

```text
universe.point_in_time = true
universe.survivorship_bias = false
股票数量 >= 500（全市场建议远高于此）
price_period_coverage >= 90%
历史板块映射和板块行情覆盖充分
```

如果达不到，系统仍可跑诊断，但会将实验标为 `DIAGNOSTIC_ONLY`。

## 5. 50只/小样本 Smoke Test

只验证“程序和真实 MCP 数据结构是否跑通”，**不要拿这个收益结论评价策略**。

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --max-universe 50 \
  --experiment baseline \
  --experiment no_triple_golden_cross
```

完成后：

```bash
.venv/bin/a-share-agent --root "$PWD" research-latest
```

## 6. 确定性完整 A/B / 消融测试

确认 Smoke Test 无错误后，扩大股票池。若 Intel 已提供严格 Point-in-Time 股票池，推荐：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --universe-mode strict_point_in_time \
  --experiment baseline \
  --experiment no_triple_golden_cross \
  --experiment core_signal_focus \
  --experiment router_disabled \
  --experiment sector_disabled
```

如果暂时没有历史股票池接口，可先：

```text
--universe-mode prefer_point_in_time
```

但报告可能为 `DIAGNOSTIC_ONLY`。

### 五个实验含义

`baseline`：当前确定性策略 + Router。

`no_triple_golden_cross`：只移除三线金叉，用于测量它对组合的边际贡献。

`core_signal_focus`：只保留 `single_bull_hold + high_volume_breakout`，仅用于诊断当前报告中表现相对较好的信号，不代表自动晋级生产。

`router_disabled`：关闭市场/板块 Strategy Router 门控，测量 Router 的真实贡献。

`sector_disabled`：将板块上下文中性化，测量当前板块层的贡献。

## 7. LLM A/B 测试

### 7.1 先检查 LLM

```bash
.venv/bin/a-share-agent --root "$PWD" llm-probe
```

### 7.2 推荐只跑明确的 A/B，不要第一次就对所有实验启用 LLM

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --include-llm \
  --experiment baseline \
  --experiment llm_gate_baseline \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

LLM Gate 的原则：

```text
历史确定性信号 -> 已达最低评分 -> LLM只做 PASS/WATCH/REJECT -> 次日成交
```

LLM **不能创造新的股票、不能修改历史价格、不能修改 deterministic score、不能直接下单**。

### 防历史知识穿越

默认：

```yaml
llm_filter_anonymize_symbol: true
```

模型看到匿名证券ID，不看到真实 ticker，从而降低模型参数记忆未来事件造成的污染风险。

LLM 输入只包含当日及之前的：

- 最多40根日线；
- 当时市场 Regime；
- 当时板块上下文；
- 确定性信号证据；
- 确定性 score / stop / route。

不会在历史回测中调用实时新闻、实时行情或 Web 搜索。

### LLM 成本与复现

每次结果按下面内容生成缓存 key：

```text
model + system prompt + schema + actual symbol + point-in-time payload
```

缓存目录：

```text
data/backtest/llm_cache/
```

同模型、同 Prompt、同历史输入重复跑时优先复用缓存。

## 8. 报告重点看什么

不要只看 Total Return。

### 收益与风险

```text
total_return
cagr
max_drawdown
max_drawdown_days
sharpe
sortino
calmar
```

### 交易质量

```text
closed_trades
win_rate
profit_factor
expectancy_pct
avg_win_pct
avg_loss_pct
max_consecutive_losses
avg_mfe_pct
avg_mae_pct
```

### Gross / Cost / Net

v0.6 新增：

```text
gross_pnl_before_costs
round_trip_fees
estimated_slippage_cost
net_realized_pnl
gross_return_on_initial
net_realized_return_on_initial
```

用于判断“策略本身没有 Alpha”还是“毛收益存在但被换手和成本吃掉”。

### 分层

```text
by_strategy
by_family
by_route
by_sector
by_market_regime
by_sector_strength
```

### 月收益30%只是报告指标

继续查看：

```text
months_ge_target
months_ge_target_rate
best_month
worst_month
```

不能为了追求30%而放宽风险参数。

## 9. Research Grade / Diagnostic Only

每个实验 `report.json` 都包含：

```text
research_validity.grade
research_validity.reasons
```

常见 `DIAGNOSTIC_ONLY` 原因：

```text
tested_symbols 太少
current universe / survivorship bias
sector membership 不是 point-in-time
板块交易几乎全部 NEUTRAL_SECTOR
sector history 缺失
```

只有数据质量先过关，再讨论策略收益。

## 10. 反馈给我哪些文件

### 首选（一个文件）

```text
data/research/runs/<suite_id>/feedback_bundle.zip
```

这个 ZIP 自动包含：

```text
research_summary.json
experiment_metrics.csv
FEEDBACK_README.md
experiments/<experiment>/report.json
experiments/<experiment>/trades.csv
experiments/<experiment>/monthly_returns.csv
experiments/<experiment>/rejections.csv
```

### 同时最好再提供

```text
data/diagnostics/latest_research_preflight.json
```

这样我可以先判断数据质量，再比较策略。

### 如果反馈上传大小受限

按优先级提供：

1. `research_summary.json`
2. `experiment_metrics.csv`
3. `latest_research_preflight.json`
4. baseline 的 `report.json`
5. LLM A/B 的两个 `report.json`

## 11. 不要提供

不要发送：

```text
.env
任何 API Key
Basic Auth 密码
飞书 Secret
```

## 12. 一键脚本

确定性验证：

```bash
bash scripts/run_research_validation.sh
```

需要 LLM A/B：

```bash
INCLUDE_LLM=1 bash scripts/run_research_validation.sh
```

可覆盖：

```bash
START_DATE=2024-10-01 \
END_DATE=2026-09-30 \
UNIVERSE_MODE=prefer_point_in_time \
MAX_UNIVERSE=500 \
INCLUDE_LLM=1 \
bash scripts/run_research_validation.sh
```

脚本结束会打印 `feedback_bundle.zip` 的准确位置。

---

# v0.6.1 LLM Gate 复测要求

先阅读 `AUDIT_REVIEW_GEMINI.md`。

旧版 LLM 三个月结果存在大量 `LLM_FILTER_ERROR -> REJECT`，不得继续作为 LLM Alpha 证据。
v0.6.1 以后只有 `llm_filter_stats.failures == 0` 且 `error_candidates == 0` 的 LLM 实验才允许比较收益。

推荐先运行：

```bash
bash scripts/reset_llm_research_cache.sh
```

然后按 `AUDIT_REVIEW_GEMINI.md` 中 B -> C -> D 顺序测试。
