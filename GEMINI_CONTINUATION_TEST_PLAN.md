# Gemini 接力测试计划 — v0.6.1

> **给接手测试的 Gemini/LLM：请先完整阅读本文件，再执行任何回测。**
>
> 本轮任务不是“证明系统赚钱”，而是验证 v0.6.1 修复后的 LLM A/B 与 Sector 数据链是否可信。禁止跳过有效性检查直接根据收益率给策略结论。

---

# 0. 必读文件顺序

按这个顺序阅读：

1. `GEMINI_CONTINUATION_TEST_PLAN.md`（本文件）
2. `CHANGELOG_V0.6.1.md`（修复原因与代码语义）
3. `AUDIT_REVIEW_GEMINI.md`（上一轮 Gemini 审计的复核）
4. `TESTING_GUIDE.md`（完整命令与输出目录）
5. `BACKTEST_DATA_QUALITY.md`（数据质量等级）
6. `RESEARCH_LAB.md`（A/B 实验语义）
7. `IMPLEMENTATION_STATUS.md`（当前已实现/未实现）

如文档与代码不一致，以代码和实际报告为准，并记录差异。

---

# 1. 不要复用旧结果

旧三个月 LLM 回测存在高失败率，**不能作为新的基准结果直接沿用**。

复测前：

```bash
cd /opt/a-share-agent
bash scripts/reset_llm_research_cache.sh
```

确认 `data/backtest/llm_cache/` 旧缓存已清理。

不要删除历史行情缓存，除非确认缓存数据本身错误。

---

# 2. 环境确认

```bash
cd /opt/a-share-agent
.venv/bin/python -c 'import a_share_agent; print(a_share_agent.__version__)'
```

必须输出：

```text
0.6.1
```

然后：

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production mcp-probe --service intel
.venv/bin/a-share-agent --root "$PWD" llm-probe
```

如果 Intel 或 LLM probe 失败，不要继续 LLM A/B。

记录诊断文件位置：

```text
data/diagnostics/
```

---

# 3. 第一步：三个月 Research Preflight

先用与旧 LLM 实验相同的日期，便于可比：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-preflight \
  --start 2026-07-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --sample-size 30
```

报告：

```text
data/diagnostics/latest_research_preflight.json
```

## 3.1 必须记录这些字段

- universe source
- universe symbol count
- point_in_time
- survivorship_bias
- price_period_coverage
- sector_mapping_coverage
- sector_history_period_coverage
- benchmark covers_period
- provider_warnings
- research_grade_candidate
- sector diagnostics（若存在）

## 3.2 Sector 判定规则

### 如果 sector_mapping_coverage > 0

继续记录具体覆盖率，并检查 sector history。

### 如果仍等于 0

**停止解释任何 Sector Router Alpha。**

不要说“sector router 有效/无效”。应：

1. 保存 `latest_research_preflight.json`；
2. 抽取其中 `sector_diagnostic`；
3. 查看真实 `mcp_intel_tdx_f10` 响应的非敏感 key 结构；
4. 提交给开发者继续补 parser。

Market Router 与 Sector Router 必须分开评价。

---

# 4. 第二步：三个月确定性对照

运行：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2026-07-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --experiment baseline \
  --experiment no_triple_golden_cross
```

目的不是追求收益，而是验证修复后 deterministic pipeline 与旧结果大体一致。

## 4.1 必须比较

Baseline vs no_triple：

- total_return
- max_drawdown
- profit_factor
- expectancy_pct
- closed_trades
- gross_pnl_before_costs
- round_trip_fees
- estimated_slippage_cost
- net_realized_pnl
- benchmark_return
- excess_return_vs_benchmark

同时记录 `research_validity.grade` 和 reasons。

如果两个实验的 universe / tested_symbols / period 不一致，A/B 不能直接解释。

---

# 5. 第三步：最关键的有效 LLM A/B

本轮**不要优先跑** `baseline vs llm_gate_baseline`。

先消除已经有强负面证据的 `triple_golden_cross`，比较：

```text
A: no_triple_golden_cross
B: llm_gate_no_triple
```

命令：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2026-07-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --include-llm \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

---

# 6. LLM 实验的硬有效性门槛

**先看工程质量，再看收益。**

找到 `llm_gate_no_triple` 对应的 `report.json`。

必须：

```text
methodology.llm_filter_stats.failures == 0
methodology.llm_filter_stats.error_candidates == 0
research_validity.llm_experiment_valid == true
```

建议同时检查：

```text
call_failure_rate == 0
candidate_error_rate == 0
```

## 6.1 任何错误 > 0 时

结论必须写：

```text
LLM A/B INVALID — transport/schema failure contaminated experiment
```

此时：

- 不评价 LLM Alpha；
- 不比较“少亏了多少”作为模型能力；
- 不因此改仓位；
- 报告错误类型、次数、平均调用时间；
- 先解决接口稳定性再重跑。

---

# 7. LLM A/B 有效后，应该怎么解释

LLM 组不是只看 total_return。

至少比较 A/B：

1. `total_return`
2. `profit_factor`
3. `expectancy_pct`
4. `max_drawdown`
5. `closed_trades`
6. `win_rate`
7. gross PnL
8. costs
9. net PnL
10. MAE / MFE（若报告提供）

同时看 LLM：

- PASS 数量
- WATCH 数量
- REJECT 数量
- ERROR 数量（必须 0）
- API calls
- avg_api_call_seconds
- cache_hits

### 不允许的解读

```text
交易少了 = LLM更聪明
回撤小了 = LLM必然有效
亏得少 = LLM产生Alpha
```

### 合理的解读方式

要判断 LLM Gate 是否真正改善**单位交易质量**：

- expectancy 是否提升；
- PF 是否提升；
- 被拒绝交易后来是否确实显著更差；
- 最大回撤改善是否只是因为暴露下降；
- 在相同风险预算下是否仍有改善。

---

# 8. 抽检 LLM 决策质量

有效 LLM A/B 完成后，从 cache/report 中随机抽检：

- 5 个 PASS
- 5 个 REJECT
- 如果有 WATCH，至少 3 个 WATCH

检查：

1. 是否只引用 `as_of` 当时可见特征；
2. 是否出现未来事件/未来价格知识；
3. 是否把匿名 security id 当作真实 ticker 推断；
4. 理由是否与输入特征一致；
5. REJECT 是否只是重复 deterministic score，而没有增量判断。

不要暴露或要求输出模型私有思维链。只审计最终结构化理由字段。

---

# 9. 三个月通过后才跑两年 LLM

只有满足：

```text
三个月 LLM failures = 0
三个月 error_candidates = 0
LLM schema stable
平均调用延迟可接受
```

才运行两年：

```bash
.venv/bin/a-share-agent \
  --root "$PWD" \
  --backend production \
  research-suite \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --universe-mode prefer_point_in_time \
  --include-llm \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

如果 Intel 已提供真正 point-in-time universe，改为：

```text
--universe-mode strict_point_in_time
```

---

# 10. 不要擅自做的策略修改

在下一轮有效报告出来前，不要自动修改生产配置为：

- 单票 30%~40%；
- 总仓 80%~100%；
- 1~3 天强制超短；
- 月收益30%硬目标；
- 去掉所有非 high_volume_breakout / single_bull_hold 信号；
- 把 sector router 宣布为已验证。

如要测试这些，只能新建**独立 research experiment**，不能覆盖 baseline。

---

# 11. 推荐新增实验的方式

如果 Gemini 认为某个改法值得测：

1. 不改 baseline；
2. 在 `config/research.yaml` 新增 experiment id；
3. 写清楚唯一变量；
4. 与 baseline/no_triple 做 A/B；
5. 报告 delta；
6. 不自动晋级 production。

示例：

```yaml
- id: no_triple_short_hold
  description: "Ablation: no triple golden cross + max holding days X"
  overrides:
    disabled_strategies: [triple_golden_cross]
    max_holding_days: X
    llm_filter_enabled: false
```

一次实验尽量只改变一个主要因素，避免无法归因。

---

# 12. 报告文件在哪里

## Preflight

```text
data/diagnostics/latest_research_preflight.json
```

## 每个单独回测

```text
data/backtest/runs/<bt-run-id>/
```

重要文件：

```text
report.json
report.html
trades.csv
rejections.csv
monthly_returns.csv
strategy_breakdown.csv
route_breakdown.csv
sector_breakdown.csv
```

## Research Suite

```text
data/research/runs/<research-suite-id>/
```

重要文件：

```text
research_summary.json
experiment_metrics.csv
FEEDBACK_README.md
feedback_bundle.zip
```

最新路径：

```bash
.venv/bin/a-share-agent --root "$PWD" research-latest
```

或：

```text
data/research/runs/latest.json
```

---

# 13. 测完后提供给 ChatGPT 的文件

**必须：**

1. `data/diagnostics/latest_research_preflight.json`
2. 最新 `data/research/runs/<suite_id>/feedback_bundle.zip`

如果 LLM 有任何失败，再补：

3. 对应 LLM 实验的 `report.json`
4. `data/logs/` 中与 LLM/研究运行相关的错误日志（请先确认无凭证）

不要提供 `.env`。

---

# 14. Gemini 最终测试报告固定模板

请按下面结构输出，不要用“老法师”“毫无争议”“必赚”等语言。

```markdown
# v0.6.1 Research Validation Report

## A. Environment
- code version:
- period:
- universe mode:
- tested symbols:
- point-in-time universe:
- survivorship bias:
- sector mapping coverage:
- sector history coverage:

## B. Deterministic A/B
### baseline
- total return:
- PF:
- expectancy:
- max drawdown:
- trades:

### no_triple_golden_cross
- ...

### delta
- ...

## C. LLM Reliability
- model:
- payload_mode:
- API calls:
- failures:
- call_failure_rate:
- candidates_reviewed:
- error_candidates:
- candidate_error_rate:
- avg_api_call_seconds:
- schema errors:

## D. LLM A/B
### no_triple
- ...

### llm_gate_no_triple
- ...

### delta
- return:
- PF:
- expectancy:
- maxDD:
- trade count:

## E. Validity
- research_validity.grade:
- llm_experiment_valid:
- reasons:

## F. Decision Audit Samples
- PASS samples:
- REJECT samples:
- WATCH samples:

## G. Conclusions Supported by Data
1.
2.
3.

## H. Conclusions NOT Yet Supported
1.
2.
3.

## I. Recommended Next Experiments
- experiment id:
- single variable changed:
- hypothesis:
- pass/fail criterion:
```

---

# 15. 本轮成功标准

本轮优先级：

```text
P0: LLM API / Schema error = 0
P1: Sector mapping 不再是 0，或拿到可修 parser 的真实结构证据
P2: no_triple A/B 可复现
P3: 得到有效 no_triple vs llm_gate_no_triple A/B
P4: 三个月稳定后才扩两年
```

不要因为 P4 收益高低而跳过 P0/P1/P2/P3。
