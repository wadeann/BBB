# v0.6.1 Research Integrity Patch — 修复记录

> 本文件记录从 v0.6 → v0.6.1 的研究可信度修复。它不是收益结论，而是解释“为什么旧三个月 LLM A/B 不能作为有效证据、代码具体修了什么、如何验证修复生效”。

## 1. 背景：旧三个月 LLM A/B 为什么判定无效

旧实验（2026-07-01 ~ 2026-09-30）出现：

- LLM API 调用 15 次，失败 12 次；
- 调用失败率约 80%；
- 24 个候选中 16 个因 `LLM_FILTER_ERROR` 被排除；
- 旧语义把 transport/schema error 与模型主动 `REJECT` 混在一起；
- 因而“交易从 10 笔降到 3 笔、亏损和回撤下降”混入了**接口失败导致的被动空仓**。

因此旧 LLM A/B 只能用于发现工程问题，不能用于证明 LLM 产生 Alpha。

---

## 2. 修复 A：LLM ERROR 与 REJECT 彻底分离

### 旧问题

旧逻辑中：

```text
LLM timeout / HTTP error / schema error
        ↓
被当作 REJECT
        ↓
候选不交易
        ↓
收益/回撤看起来可能改善
```

这会把系统故障伪装成模型能力。

### v0.6.1 行为

研究模式内：

```text
模型主动拒绝       -> decision=REJECT
超时/网络/Schema错 -> decision=ERROR
```

`ERROR` 候选仍不会成交（研究安全优先），但：

- rejection reason 记录为 `LLM_ERROR_EXCLUDED`；
- `llm_filter_stats.failures` 增加；
- `llm_filter_stats.error_candidates` 增加；
- `research_validity.llm_experiment_valid=false`；
- 整个实验必须视为 `DIAGNOSTIC_ONLY`，禁止解释收益差异为 LLM Alpha。

### 代码位置

- `a_share_agent/backtest/llm_filter.py`
- `a_share_agent/backtest/research.py`
- `a_share_agent/backtest/engine.py`

### 必须通过的验收

LLM A/B 只有在同时满足下面三条时，才允许解释模型贡献：

```text
methodology.llm_filter_stats.failures == 0
methodology.llm_filter_stats.error_candidates == 0
research_validity.llm_experiment_valid == true
```

任何一条不满足：本轮 LLM A/B 作废，先修服务/超时/Schema，不讨论收益。

---

## 3. 修复 B：LLM 返回值本地 JSON Schema 强校验

### 旧问题

供应商即使配置 `json_object`，也只保证“是 JSON”，并不保证字段齐全。旧缓存曾出现缺少：

- `confidence`
- `reasons_for`
- `risk_flags`

但仍进入研究链。

### v0.6.1 行为

每批 LLM 返回必须满足本地 `BATCH_SCHEMA`，并且：

- 每个 `candidate_id` 必须完整对应输入；
- 缺字段、枚举错误、candidate set 不匹配均判 `ERROR`；
- 旧不合规缓存自动失效，不静默复用。

### 验收

查看 LLM 实验 `report.json`：

```text
methodology.llm_filter_stats.error_candidates == 0
```

并确认没有因 Schema 异常产生的研究有效性告警。

---

## 4. 修复 C：LLM 历史输入从 40 根完整 K 线改为 compact features

### 旧问题

旧回测将大量 OHLCV JSON 送入 LLM：

- Token 大；
- 推理慢；
- DeepSeek reasoning 模型容易触碰网关 timeout；
- retry 会放大总耗时。

### v0.6.1 默认输入

历史时点只提供结构化特征（都只能由 `as_of` 当日及之前数据计算）：

- 5/10/20/40 日收益；
- MA5、MA20 距离；
- MA20 slope proxy；
- 5/20 日量能比；
- 当日成交量 / 20 日均量；
- 距 20 日高点；
- 从 20 日低点反弹幅度；
- ATR proxy；
- stop distance；
- deterministic signal / score / route / market / sector。

代码：`a_share_agent/backtest/llm_filter.py::_compact_features`

默认：

```text
payload_mode = compact_features
```

这仍保留“下降趋势 / 追高 / 量能确认 / 止损脆弱性”的信息，但显著减少输入。

---

## 5. 修复 D：只审核真正可能占用仓位的候选

### 旧问题

过去可能先把当天 Top-N 全部交给 LLM，然后才发现组合没有足够仓位，造成无意义调用。

### v0.6.1

先计算当日可新增仓位容量，再从排名最高候选向下审核：

```text
当前可新增 2 个仓位
↓
先审排名1
PASS -> 占1个
↓
审排名2
REJECT -> 继续排名3
↓
直到得到2个 PASS 或达到候选上限
```

目的：减少 API 次数，同时不改变原 deterministic 排名。

---

## 6. 修复 E：Research LLM fail-fast，不再重试放大

Research 与 Live 分开配置。

研究回测默认：

```yaml
llm:
  research_overrides:
    timeout_seconds: 90
    retries: 0
    max_tokens: 1200
    temperature: 0
```

原则：

- Research 一旦调用失败，就准确记录失败并让该实验无效；
- 不用 retry 掩盖服务稳定性问题；
- Live/Paper Agent 的生产重试策略不受影响。

验证配置：`config/runtime.yaml`

---

## 7. 修复 F：TDX/F10 行业字段递归解析

### 旧问题

真实预检中：

```text
sector_mapping_coverage = 0
sector_history_period_coverage = 0
```

原 parser 主要查看顶层字段，真实 MCP 若返回：

```json
{"data":{"basic":{"所属行业":"...","行业代码":"..."}}}
```

就可能解析不到。

### v0.6.1

增加递归解析，兼容常见字段：

- `industry / industry_name / industry_code`
- `sector / sector_code`
- `hy_name / hy_code`
- `所属行业 / 行业 / 行业名称 / 行业代码`
- `板块 / 板块名称 / 板块代码`
- `data / result / basic` 等嵌套层

旧 `null sector` 缓存不继续复用。

若仍解析失败，preflight 会输出非敏感 key diagnostics，便于下一轮按真实 MCP JSON 继续适配。

---

## 8. 修复 G：旧 LLM Research Cache 必须重置

旧缓存可能包含：

- ERROR 被当 REJECT 的历史语义；
- 不符合新 Schema 的结果；
- 旧 payload mode。

复测前必须执行：

```bash
bash scripts/reset_llm_research_cache.sh
```

不要把旧 LLM 结果与 v0.6.1 新实验混合。

---

## 9. 本次明确没有修改的策略参数

以下建议**没有进入默认配置**：

- 单票仓位 30%~40%；
- 总仓位 80%~100%；
- 强制持有期改为 1~3 天；
- “月收益 30%”作为交易 KPI；
- 因三个月结果直接把 LLM 判定为已产生 Alpha；
- 将板块 Router 判定为已验证有效。

原因：当前历史研究仍存在小股票池/幸存者偏差/板块 point-in-time 覆盖不足等限制。先验证数据和策略，再讨论风险预算。

---

## 10. 三线金叉当前处理

`triple_golden_cross` 没有从代码删除，仍用于研究对照；但根据已有两年诊断：

- 82 笔；
- 胜率约 24%；
- 累计明显负贡献；
- `no_triple_golden_cross` 显著优于 baseline。

因此建议：

```text
Research: 保留用于 ablation
Production candidate strategy: 暂停启用
```

只有在更完整的 Point-in-Time 全市场数据上重新验证后，才考虑恢复或重构。

---

## 11. 修复后最重要的目标

v0.6.1 第一目标不是获得漂亮收益，而是拿到第一组**有效实验**：

```text
A = no_triple_golden_cross
B = llm_gate_no_triple
```

要求：

- 相同历史日期；
- 相同 universe；
- 相同成交模型；
- 相同成本；
- B 唯一新增因素是 LLM Gate；
- LLM 调用错误必须为 0。

只有这组 A/B 有效后，才能判断 LLM 是：

1. 提高期望值；
2. 降低最大回撤；
3. 只降低交易频率；
4. 或没有稳定增量价值。
