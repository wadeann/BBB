# 给 Gemini 的启动提示词

把本项目完整解压后，请严格执行以下任务，不要跳步：

1. **先阅读**：
   - `GEMINI_HANDOFF_V07.md`（最高优先级）
   - `CHANGELOG_V0.7.md`
   - `FULL_MARKET_BACKTEST.md`
   - `MCP_HISTORICAL_DATA_CONTRACT.md`
   - `VALIDATION_REPORT_TEMPLATE.md`

2. 你的首要目标不是优化收益，而是验证 v0.7 的 **Full-Market Point-in-Time 历史数据链和回测真实性**。

3. 不要在数据链达到 Research Grade 前修改：
   - 仓位上限
   - 止损参数
   - score 门槛
   - Strategy Router 权重
   - 策略启停
   - 月收益目标

4. 按以下 Phase 顺序执行：

```bash
PHASE=test      bash scripts/run_gemini_v07_validation.sh
PHASE=mcp       bash scripts/run_gemini_v07_validation.sh
PHASE=preflight bash scripts/run_gemini_v07_validation.sh
PHASE=smoke     bash scripts/run_gemini_v07_validation.sh
PHASE=perf      bash scripts/run_gemini_v07_validation.sh
PHASE=full3m    bash scripts/run_gemini_v07_validation.sh
PHASE=llm3m     bash scripts/run_gemini_v07_validation.sh
```

只有 `preflight + full3m` 达到 `RESEARCH_GRADE` 后才能：

```bash
PHASE=full2y bash scripts/run_gemini_v07_validation.sh
```

5. 如果 `strict_point_in_time` 无法取得历史全市场 Universe：
   - **停止正式收益测试**；
   - 按 `MCP_HISTORICAL_DATA_CONTRACT.md` 补 Intel MCP；
   - 不得退回 `universe.txt` 并继续宣称“全市场回测”。

6. LLM A/B 只有满足以下全部条件才允许解释：

```text
failures == 0
error_candidates == 0
call_failure_rate == 0
llm_experiment_valid == true
```

7. 完成后：
   - 复制 `VALIDATION_REPORT_TEMPLATE.md` 为 `VALIDATION_REPORT_COMPLETED.md`；
   - 按模板完整填写，不删除任何章节；
   - 必须区分 `Supported Conclusions` 与 `Unsupported Conclusions`；
   - 运行：

```bash
PHASE=collect bash scripts/run_gemini_v07_validation.sh
```

8. 最后给用户保留并明确指出以下文件位置：

```text
data/diagnostics/latest_research_preflight.json
data/research/gemini_feedback_*.zip
VALIDATION_REPORT_COMPLETED.md
```

9. 如果任何阶段失败，不要用猜测补齐结果。报告：
   - 失败命令
   - exception / HTTP status
   - MCP tool name
   - 原始返回结构（脱敏）
   - 应修复的代码/接口
   - 修复后重新从失败 Phase 开始验证

10. 不要因为目标是“月收益30%”而扩大仓位或降低风控。先证明策略在 Full-Market PIT + 成本 + OOS 条件下具有正期望。
