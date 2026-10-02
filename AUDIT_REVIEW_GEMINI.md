# v0.6.1 对 Gemini 审计与三个月 LLM 回测的复核

## 结论摘要

这次结果里有三类结论必须分开：

1. **三线金叉在当前样本中明显拖累**：两年 63 只静态股票池诊断里，`triple_golden_cross` 82 笔，累计亏损约 6.13 万，胜率约 24%。`no_triple_golden_cross` 显著优于原 baseline。它应继续保留为对照实验，但不建议作为候选生产策略启用，直到更完整 point-in-time 股票池复核。
2. **Market Strategy Router 有防守价值的迹象**：`router_disabled` 明显恶化。但这不能归因于“板块 Router”，因为本次 preflight 的历史板块映射覆盖率为 0%，`sector_disabled` 与 baseline 完全相同。当前证据支持的是“市场 Regime 门控”，不是板块 Alpha。
3. **三个月 LLM Gate 结果无效，不能证明 LLM 有增量 Alpha**：该运行 15 次 API 调用中 12 次失败；24 个候选里 16 个因 `LLM_FILTER_ERROR` 被排除。旧代码把调用失败当作 `REJECT`，所以交易数从 10 降到 3、回撤下降，混入了“网络失败导致被动空仓”的效果。

因此 Gemini 关于“LLM 防御识别力已经得到充分佐证”的判断过度。三笔成交、PF=0.21，再叠加 80% API 调用失败，不能作为有效 A/B 证据。

## v0.6.1 已修复

### 1. LLM 调用失败不再算智能 REJECT

研究模式中，超时、网络、Schema 错误统一返回内部 `decision=ERROR`：

- 候选仍会被排除以保证研究安全；
- 但报告会记录 `LLM_ERROR_EXCLUDED`；
- `research_validity` 自动加入 LLM 错误原因；
- `llm_experiment_valid=false`；
- 不允许把这类实验解释成 LLM 策略表现。

### 2. 增加本地 JSON Schema 强校验

即使供应商只支持 `json_object`，返回内容仍必须满足本地 Schema。
旧缓存里缺少 `confidence / reasons_for / risk_flags` 的结果会自动视为无效缓存，不再复用。

### 3. LLM 历史输入默认改为 compact_features

不再默认发送 40 根完整 OHLCV JSON。默认发送历史时点特征：

- 5/10/20/40 日收益；
- MA5/MA20 距离；
- MA20 斜率代理；
- 5/20 日量能比；
- 当日量比；
- 距 20 日高点；
- 20 日低点反弹幅度；
- ATR 代理；
- 止损距离；
- 原确定性信号、Score、Market/Route/Sector 上下文。

这保留“识别下降趋势、追高、止损脆弱性”的信息，同时显著缩小 Token 输入。

### 4. 只审核真正可能占用仓位的候选

旧逻辑每天先审 Top20，再看组合是否还有仓位，浪费大量 LLM 请求。
新逻辑先计算 `room + max_new`，从排名最高候选开始小批审核；只在前面的候选被拒绝时继续向下审核，直到仓位容量填满或达到 Top-N 上限。

### 5. Research LLM 单独 fail-fast

`config/runtime.yaml` 新增 `llm.research_overrides`：

- timeout: 90s
- retries: 0
- max_tokens: 1200
- temperature: 0

实时 Agent 的 LLM 配置不受影响。研究回测不再因为 `retries=2` 把一次超时放大数分钟。

### 6. 增强 TDX F10 行业字段解析

旧 parser 只读取顶层或 `basic`，本次真实数据全部解析为 `code=null`。v0.6.1 增加递归解析，支持嵌套 `data/basic`、英文/常见中文行业字段，并给无法识别的响应写入非敏感 key 诊断。

旧的 null sector cache 会自动重新查询，不再继续污染测试。

## 暂时不做的修改

以下 Gemini 建议**没有足够数据支持，暂不进入生产默认值**：

- 单票仓位从 10% 直接提高到 30%~40%；
- 总仓从 50% 提高到 80%~100%；
- 强制把持有期改成 1~3 天；
- 因“三个月表现”就认定 LLM 已经有 Alpha；
- 认为 sector router 已验证有效。

这些都必须在动态 point-in-time 股票池、真实历史板块映射、零 LLM 调用错误的条件下做独立 A/B。

## 推荐测试顺序

### A. 先清理旧 LLM 研究缓存

```bash
bash scripts/reset_llm_research_cache.sh
```

### B. 重新跑数据预检

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production research-preflight \
  --start 2026-07-01 --end 2026-09-30 \
  --universe-mode prefer_point_in_time --sample-size 30
```

重点看：

- `sector_mapping_coverage`
- `sector_history_period_coverage`
- `universe.point_in_time`
- `survivorship_bias`

### C. 三个月确定性基线

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production research-suite \
  --start 2026-07-01 --end 2026-09-30 \
  --experiment baseline \
  --experiment no_triple_golden_cross
```

### D. 三个月 LLM 增量 A/B（最关键）

优先比较 `no_triple_golden_cross` 与 `llm_gate_no_triple`，这样不会让已知劣质三线金叉干扰 LLM 增量测试。

```bash
.venv/bin/a-share-agent --root "$PWD" --backend production research-suite \
  --start 2026-07-01 --end 2026-09-30 \
  --include-llm \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

只有同时满足下面条件，才允许解释 LLM 效果：

```text
llm_filter_stats.failures == 0
llm_filter_stats.error_candidates == 0
research_validity.llm_experiment_valid == true
```

否则这次 LLM A/B 自动判为无效，先修连接/模型输出，不讨论收益。

### E. 稳定后再跑两年

三个月连续零错误后再跑 2024-10-01 ~ 2026-09-30，避免再次花数小时得到不可解释的结果。

## 反馈给 ChatGPT 的文件

优先上传：

1. `data/research/runs/<suite_id>/feedback_bundle.zip`
2. `data/diagnostics/latest_research_preflight.json`

若是 LLM 实验，再附：

3. 对应实验 `report.json`（feedback bundle 已包含）
4. `report.json -> methodology.llm_filter_stats`

这四项足够判断下一轮是否应调整策略、Router、LLM Gate 或仓位。
