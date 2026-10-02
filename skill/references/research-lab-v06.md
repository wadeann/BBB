# Research Lab v0.6

Research Lab 用于标准化比较同一历史数据上的策略、Router 和 LLM Gate，不负责自动晋级生产策略。

## 标准实验

- baseline
- no_triple_golden_cross
- core_signal_focus
- router_disabled
- sector_disabled
- llm_gate_baseline（可选）
- llm_gate_no_triple（可选）

所有实验必须使用相同：

- 历史时间段；
- 股票池数据版本；
- 行情数据；
- 初始资金；
- 成交规则；
- 费用与滑点；
- Risk 参数。

只改变实验明确指定的变量。

## LLM Gate

LLM 只能对 deterministic candidate 输出 PASS/WATCH/REJECT。

禁止：

- 新增不在候选池的股票；
- 修改历史价格、score、market/sector facts；
- 访问实时 MCP / Web / News；
- 直接产生历史成交；
- 用真实 ticker 作为默认输入。

默认 ticker 匿名化。模型错误 fail-closed 为 REJECT。

## 数据质量

每个实验必须带 `research_validity`：

```text
RESEARCH_GRADE
DIAGNOSTIC_ONLY
```

存在股票覆盖不足、幸存者偏差、非 Point-in-Time sector membership、过高 neutral-sector share 等问题时，必须降级。

## 反馈包

Research Suite 必须输出：

```text
research_summary.json
experiment_metrics.csv
FEEDBACK_README.md
feedback_bundle.zip
```

不得自动根据“收益最高实验”修改生产配置。
