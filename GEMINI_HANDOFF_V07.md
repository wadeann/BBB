# Gemini 接力开发与验证总说明 — v0.7 Full-Market PIT

> **Gemini/其他 LLM 接手本包时，先完整阅读本文件。**
> 本轮目标不是调高收益，也不是扩大仓位，而是验证：**历史全市场动态选股链是否真实、无幸存者偏差、板块信息是否按历史时点生效，以及 LLM Gate 是否在可靠条件下产生增量价值。**

---

## 0. 本轮禁止事项

在 Full-Market PIT 数据链达到 Research Grade 前，不要：

- 把单票仓位从 10% 提高到 30%~40%；
- 把总仓位提高到 80%~100%；
- 以“月收益 30%”作为强制 KPI；
- 因 3 个月或少量股票结果直接删除/永久保留策略；
- 使用 `universe.txt` / 当前问财股票池替代正式历史全市场；
- 使用今天的 F10 行业归属解释两年前板块；
- 把 LLM timeout / schema error / transport error 当成 REJECT；
- 在 `DIAGNOSTIC_ONLY` 结果上宣称“已验证长期盈利”。

---

# 1. v0.7 相对 v0.6.1 的核心修改

## 1.1 回测股票池从“固定 symbols”改为“每日动态 Point-in-Time Universe”

### 旧行为

回测开始时确定一组 `symbols`，整个周期重复扫描同一批股票。即使使用 historical bars，也可能产生：

- 幸存者偏差；
- 未来上市股票提前进入历史；
- 后来退市股票从历史消失；
- 历史 ST/停牌/退市整理状态失真。

### v0.7 行为

回测引擎按 benchmark 交易日逐日调用：

```python
HistoricalDataProvider.active_records_on(date)
```

每个历史交易日独立得到当时可交易集合，再进入策略筛选。

### 必须验证

随机抽查至少 5 个历史日期：

1. 当天 active universe 数量合理；
2. 当时尚未上市股票不出现；
3. 当时已上市、后来退市的股票仍可在其有效期内出现；
4. 当日 ST/停牌/退市整理状态能过滤；
5. 每天 `universe_hash` 可复现。

---

## 1.2 新增历史 Universe 双路径

Runtime 按优先级使用：

1. `data/backtest/security_master.csv`（有效期区间）
2. Intel MCP interval fast path
3. Intel MCP daily PIT paginated path

推荐 MCP：

```text
mcp_intel_get_historical_universe
```

支持两种模式：

### Interval fast path

```json
{
  "start_date": "2024-10-01",
  "end_date": "2026-09-30",
  "mode": "membership_intervals",
  "include_status": true
}
```

### Daily PIT path

```json
{
  "date": "2025-03-17",
  "market": "A_SHARE",
  "page": 1,
  "limit": 500
}
```

详细契约见：

`MCP_HISTORICAL_DATA_CONTRACT.md`

### 严格规则

正式收益验证必须：

```text
--universe-mode strict_point_in_time
max_universe = 0
```

若拿不到历史 PIT Universe：**正式实验必须停止**，不得静默退回 63 只静态池。

---

## 1.3 Sector Router 改为历史时点行业归属

新增：

```python
sector_info_on(symbol, as_of)
```

优先级：

1. historical universe 直接携带 `industry_code/industry_name`
2. 历史 security master
3. `mcp_intel_get_historical_sector_membership(symbol, as_of)`
4. current F10 仅 diagnostic fallback

### 必须验证

`latest_research_preflight.json` 中：

```text
sector_mapping_coverage >= 0.80
sector_history_asof_coverage >= 0.80
```

并检查：

```text
neutral_sector_trade_share <= 0.85
```

否则 Sector Router 的收益贡献不能解释。

---

## 1.4 合并 Gemini commit e2d700a 的 LLM structured-output 修复

已合并以下修改：

- Prompt 明确根对象：`{"decisions": [...]}`
- `structured_output=json_schema`
- 兼容供应商返回 `candidates` 根字段
- 兼容 `candidate_id -> object` map
- `confidence` 若返回 0~1，自动转换到 0~100
- Research profile：
  - timeout 180s
  - retries 1
  - max_tokens 4096
  - temperature 0

同时保留 v0.6.1 的安全修复：

- 本地 JSON Schema 强校验；
- transport/schema error = `ERROR`，不能转换为 `REJECT`；
- `failures > 0` 或 `error_candidates > 0` 时，LLM 实验不得解释；
- anonymous `security_id`；
- compact historical features；
- LLM 不能创建新信号，只能 PASS/WATCH/REJECT deterministic candidates。

### 必须验证

LLM A/B 可解释前：

```text
llm_filter_stats.failures == 0
llm_filter_stats.error_candidates == 0
llm_filter_stats.call_failure_rate == 0
research_validity.llm_experiment_valid == true
```

---

## 1.5 报告新增 Full-Market 数据质量字段

正式报告应包含：

```text
dynamic_universe_days
active_universe_min
active_universe_avg
active_universe_max
point_in_time_universe_all_days
dynamic_universe_daily
sector_mapping_coverage
historical_sector_mapping_coverage
universe_hash / dataset_version
fallback_days / degraded_days
```

如果这些字段缺失，先修报告，不进入策略结论。

---

# 2. Gemini 必须按以下顺序操作

## Phase A — 代码与配置确认

```bash
cd /opt/a-share-agent
python3 -m venv .venv
.venv/bin/pip install -e .
PYTHONPATH=. .venv/bin/pytest -q
```

**全部 PASS 才继续。**

记录：

```text
python version
package version
git commit
test count
```

---

## Phase B — MCP 历史接口确认

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production mcp-probe --service intel
```

重点确认：

```text
mcp_intel_get_historical_universe
mcp_intel_get_historical_sector_membership
```

如果 Intel 服务尚未提供：

1. 按 `MCP_HISTORICAL_DATA_CONTRACT.md` 实现；
2. 重新跑 `mcp-probe`；
3. 不允许用 63 只 `universe.txt` 继续正式验证。

---

## Phase C — Research Preflight（必须先过）

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

### PASS 条件

至少：

```text
formal_full_market_ready = true
point_in_time = true
survivorship_bias = false
dynamic_daily = true
sector_mapping_coverage >= 0.80
sector_history_asof_coverage >= 0.80
price_period_coverage >= 0.90
```

如果 FAIL：

- 停止收益实验；
- 只修数据接口/解析/缓存；
- 不改策略参数；
- 报告失败原因和 MCP 原始字段结构。

---

## Phase D — 50只冒烟测试（绝不解释收益）

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

只验证：

- 每日 universe 变化；
- K线缓存；
- sector lookup；
- 报告生成；
- 无 exec side-effect；
- 缓存复跑一致性。

**不得写“50只测试盈利/亏损说明策略有效/无效”。**

---

## Phase E — 500只性能测试（仍不解释长期收益）

把 `--max-universe 500`。

记录：

```text
首次运行耗时
复跑耗时
MCP calls
MCP failures
price cache hit rate
universe cache hit rate
sector cache hit rate
peak RSS / memory
missing bars
```

如果性能不可接受，优先优化缓存/批量接口，不改变策略逻辑。

---

## Phase F — 三个月全市场 deterministic A/B

正式运行时 **不设置 `--max-universe`**：

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

### 解释顺序

先确认：

```text
research_validity.grade == RESEARCH_GRADE
point_in_time_universe_all_days == true
survivorship_bias == false
```

再比较：

```text
total_return
CAGR
max_drawdown
profit_factor
expectancy
win_rate
trade_count
gross_pnl_before_costs
round_trip_fees
estimated_slippage_cost
net_realized_pnl
```

同时必须按：

```text
strategy
strategy_family
market_regime
sector_strength
sector_code
route
```

拆分结果。

---

## Phase G — 三个月全市场 LLM A/B

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

先报告工程可靠性：

```text
api_calls
failures
call_failure_rate
candidates_reviewed
successful_candidates
error_candidates
candidate_error_rate
avg_api_call_seconds
PASS/WATCH/REJECT/ERROR count
```

只有 0 error 才比较投资结果。

另外必须抽检：

- 至少 5 个 PASS
- 全部 WATCH（若 <= 10）或至少 5 个 WATCH
- 全部 REJECT（若 <= 10）或至少 5 个 REJECT

记录每个 decision 的：

```text
candidate_id
as_of
signals
score
market_regime
sector
compact features
decision
confidence
reasons_for
reasons_against
risk_flags
随后真实交易/未交易表现
```

目的不是展示“模型说得像专家”，而是验证 decision 与后续结果是否有统计价值。

---

## Phase H — 两年 Full-Market PIT

只有三个月全市场 Research Grade 后才运行：

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

LLM 两年实验后置；先拿 deterministic Full-Market PIT 建立可靠基线。

---

## Phase I — Walk-Forward / OOS

只有两年 Full-Market PIT deterministic 结果稳定后才做。

禁止：

- 在同一两年样本反复调参数；
- 再用同一两年全周期宣称“样本外”；
- 根据最佳历史结果直接提高仓位。

---

# 3. Gemini 最终报告必须使用固定结构

请复制 `VALIDATION_REPORT_TEMPLATE.md` 填写，不要自由发挥省略章节。

必须区分：

## Supported Conclusions

只允许写被数据直接支持的结论。

## Unsupported Conclusions

必须明确列出仍不能支持的结论，例如：

- 不能由 3 个月推出长期盈利；
- 不能由截断 universe 推出全市场；
- 不能由 LLM 少交易推出 Alpha；
- 不能在 Expectancy <= 0 时通过加仓“解决”；
- 不能在 sector coverage 不足时评价 Sector Router。

---

# 4. 最终要回传给 ChatGPT 的文件

## 必传 1

```text
data/diagnostics/latest_research_preflight.json
```

## 必传 2

最新 Full-Market Research Suite：

```text
data/research/runs/<suite-id>/feedback_bundle.zip
```

## 必传 3

Gemini 自己填写的：

```text
VALIDATION_REPORT_COMPLETED.md
```

## 两年正式回测额外上传

基线和最佳实验的：

```text
report.json
trades.csv
rejections.csv
monthly_returns.csv
strategy_breakdown.csv
route_breakdown.csv
sector_breakdown.csv
```

## 如果发生数据/API问题，再加

```text
data/diagnostics/latest_mcp_probe.json
data/diagnostics/latest_llm_probe.json
data/logs/mcp-probe.log
data/logs/error.log
```

可运行：

```bash
bash scripts/collect_gemini_feedback.sh
```

它会把上述现有文件打成一个单独反馈 ZIP。

---

# 5. 验收优先级

优先级必须是：

```text
P0 历史数据真实性 / PIT
P1 无幸存者偏差
P2 Sector 历史覆盖
P3 回测成交与成本真实性
P4 deterministic A/B
P5 LLM reliability
P6 LLM incremental value
P7 Walk-Forward / OOS
P8 Paper Shadow
```

**收益率排在数据真实性之后。**
