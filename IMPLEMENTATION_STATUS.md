# Implementation Status — v0.5.0

## 已完成

### Worker / Scheduler
- Worker 与 Web 进程分离
- BackgroundWorker 常驻循环
- SQLite 单 Worker lease
- 持久化 `(trade_date, phase, slot)` 幂等
- Worker heartbeat / phase history
- 启动 `RECOVERY_SYNC`
- 交易日按日缓存
- 非交易日跳过
- 交易日校验失败 fail-closed

### Realtime Web
- AuditIndex cross-process SSE
- `Last-Event-ID` 续传
- 浏览器 LIVE 状态
- Audit 事件触发 Dashboard 增量刷新
- Worker 状态 API/Badge
- Nginx SSE no-buffering 模板

### Production MCP
- Streamable HTTP MCP / JSON-RPC
- per-service initialize/session
- JSON 与 `text/event-stream` 响应
- Basic / Bearer / custom header Auth
- intel/risk/exec 实际 URL 已写入 config
- secret 仅从 env 注入
- `mcp-probe`
- 服务器 tool catalog 对比与 missing/unexpected 报告

### Production LLM
- OpenAI-compatible `/v1/chat/completions`
- env `base_url/api_key/model`
- `json_schema` / `json_object` fallback
- timeout/retry
- `llm-probe`
- Web 不持有 LLM Client，Worker 才初始化 LLM
- `preflight` 聚合检查


### Notifications
- Browser SSE Toast 弹窗
- 可选浏览器原生桌面通知
- 飞书自定义机器人 Webhook
- 飞书 interactive card / text
- 可选签名校验
- `event_id + channel` 去重
- SQLite 持久化发送/重试状态
- 指数退避式重试配置
- `notification-probe` / `notification-status`
- Webhook/secret 日志脱敏

### 原有核心继续通过
- AuditEventWriter / AuditIndex / SnapshotStore
- Exact Replay / Hash Chain
- IntentStore / Local Risk / Risk MCP / Paper Exec
- Strategy Router / Signal Engine
- Dashboard / Sector / Stock / Replay Workbench

## 自动测试

```text
pytest: 23 passed
```

覆盖：
- Audit hash chain
- Execution idempotency
- Scheduler / Strategy Router
- Handoff / deterministic signal
- Dashboard / Sector / Stock / Replay
- MCP Basic Auth + MCP session + tool call
- MCP catalog mismatch detection
- OpenAI-compatible JSON client
- Worker phase-slot idempotency
- Worker non-trading-day guard
- Cross-process Audit SSE
- Notification signal filter / Feishu signed card
- Notification persistent dedup

## 需要部署环境完成的最后验证

1. 使用实际 Basic Auth 明文密码运行 `mcp-probe`；
2. 确认 intel/risk/exec `tools/list` 与 catalog 一致，或按真实服务调整 mapping；
3. 提供 LLM `base_url/api_key/model` 并运行 `llm-probe`；
4. 如启用飞书，运行 `notification-probe`；
5. 运行 `preflight`；
6. Worker 先以 paper 模式连续运行；
7. 根据真实 MCP JSON 返回样例微调字段 normalization（如有必要）。

## 保持的安全限制

- `mode: paper`
- `allow_real_execution: false`
- Web 不提供直接下单 API
- Replay 禁止 Execution
- Risk REJECT 不可被 LLM 覆盖
- 凭证不写入 Audit / README / ZIP

## v0.4.1 deployment diagnostics patch

- Project-local `.env` auto-loading; no `/etc/a-share-agent` dependency.
- Process environment variables override `.env`.
- Probe commands persist redacted reports under `data/diagnostics/` with `latest_*.json`.
- CLI process logs rotate under `data/logs/`; errors also go to `error.log`.
- systemd templates rely on project-local `.env` and no longer reference external EnvironmentFile.


## v0.5 Backtest Lab

Implemented:

- HistoricalDataProvider with disk cache and local CSV override.
- Paged current-universe retrieval plus survivorship-bias disclosure.
- Multi-benchmark historical market-regime reconstruction.
- Historical sector-strength/lifecycle reconstruction with explicit degraded fallback.
- Reuse of deterministic signal engine and StrategyRouter.
- Close-confirmed signals with next-session-open execution (look-ahead protection).
- A-share T+1, lot sizing, daily price-limit lockout, transaction cost and slippage model.
- Risk-based sizing, single/total position caps, protective stop and max-holding exit.
- MAE/MFE, equity curve, monthly returns and benchmark comparison.
- Monthly >=30% target hit count/rate (reporting only).
- Strategy/family/route/market-regime/sector-strength attribution.
- Rejection ledger for Router/score/execution/risk constraints.
- JSON/HTML/CSV reports and Backtest Lab Web APIs/UI.
- Threshold Walk-Forward engine with non-overlapping train/test windows.
- Backtest path regression test proving it does not call broker side-effect MCP tools.

Known fidelity limits disclosed in every report:

- Historical batch backtest intentionally does not replay LLM decisions.
- Current-universe fallback creates survivorship bias.
- Current F10 sector mapping may differ from historical membership.
- Historical market health / mainline is reconstructed from price data; current market-health APIs are never backfilled into the past.

## v0.5.1 documentation handoff

新增 Point-in-Time 历史股票池的正式实施规范，但**尚未把建议历史 MCP 接口实现进 Intel 服务，也尚未把 HistoricalUniverseProvider 完整编码进 Runtime**。

新增文件：

- `LLM_NEXT_STEPS.md`
- `MCP_HISTORICAL_DATA_CONTRACT.md`
- `BACKTEST_DATA_QUALITY.md`
- `skill/references/historical-point-in-time-backtest.md`
- `skill/references/mcp-historical-interface-proposal.md`

下一位 LLM/开发者应优先完成：

1. HistoricalUniverseProvider 抽象与实现。
2. strict / degraded universe mode。
3. Intel MCP 历史 Universe / Security / Sector Membership 接口适配。
4. report.json data_quality 扩展。
5. Universe hash 与 dataset version。
6. 对应自动测试与正式两年全市场回测。

完成前，不应把 current-universe 历史回测结果描述为严格的全市场 Point-in-Time 业绩验证。

---

# v0.6 Research Lab implementation status

## 已完成

### 标准 A/B / 消融实验

- `research-suite` CLI
- `config/research.yaml`
- baseline / no_triple_golden_cross / core_signal_focus / router_disabled / sector_disabled
- 可选 LLM Gate：llm_gate_baseline / llm_gate_no_triple
- 实验间共享历史数据缓存，但每个实验独立生成 backtest report
- 自动生成 comparison metrics 与 baseline delta
- 自动生成 `feedback_bundle.zip`

### LLM Historical Gate

- LLM 仅过滤 deterministic candidate，不能创建新信号
- PASS / WATCH / REJECT JSON Schema
- 仅使用 as-of 当日及之前数据
- 不调用实时 MCP / News / Web
- 出错 fail-closed = REJECT
- model + prompt + payload cache
- 默认匿名化 ticker，降低模型历史知识污染
- LLM 调用统计写入 methodology

### Point-in-Time Universe

- `data/backtest/security_master.csv` 本地历史证券主表
- `strict_point_in_time` / `prefer_point_in_time` / fallback 模式
- `eligible_on(symbol, date)` 动态历史资格过滤
- optional MCP：`mcp_intel_get_historical_universe`
- optional Intel tools 被允许调用，但不计入原始 53-tool 必选 catalog
- 缺少 PIT 数据时显式 survivorship-bias warning

### Research data preflight

- `research-preflight` CLI
- universe source / size / PIT / survivorship 检查
- 历史价格周期覆盖抽样
- sector mapping 覆盖抽样
- sector history 周期覆盖抽样
- benchmark 周期覆盖
- 自动写 `data/diagnostics/latest_research_preflight.json`

### Gross / Cost / Net

- raw entry / exit price retained
- round-trip explicit fees
- estimated round-trip slippage cost
- gross PnL before costs
- net realized PnL
- gross/net return on initial cash

### 报告与交接

- `TESTING_GUIDE.md`
- `RESEARCH_LAB.md`
- `scripts/run_research_validation.sh`
- `scripts/show_feedback_path.sh`
- research output：`data/research/runs/<suite_id>/`
- single-run output：`data/backtest/runs/<run_id>/`
- automatic `feedback_bundle.zip`

## 数据质量保护

Research Suite 会将以下情况标记为 `DIAGNOSTIC_ONLY`：

- tested symbols < 配置最小值
- static/current universe 导致 survivorship bias
- sector membership 不是 Point-in-Time
- neutral-sector trade share 过高
- sector history 缺失

因此，收益更高不等于自动晋级策略。

## 自动测试

v0.6 新增覆盖：

- Historical LLM Filter cache
- LLM historical gate deterministic output contract
- local point-in-time security master eligibility
- Research Validity downgrade logic
- Gross / Fee / Slippage / Net decomposition

最终打包前验证结果：

- `pytest`: **28 passed**
- Python compile: **PASS**
- `run_research_validation.sh` / `show_feedback_path.sh`: **bash syntax PASS**
- `research-suite --help` / `research-preflight --help`: **PASS**
- ResearchLab end-to-end CLI synthetic run: **PASS**
- `feedback_bundle.zip` generation/contents: **PASS**
- Backtest execution safety (no Exec MCP order calls): **PASS**

# v0.6.1 Research integrity patch

- [x] LLM transport/schema failure separated from intelligent REJECT
- [x] LLM experiment invalidated when any candidate is excluded by LLM ERROR
- [x] local JSON Schema enforcement for OpenAI-compatible JSON output
- [x] legacy malformed LLM caches ignored
- [x] compact historical feature payload for LLM research
- [x] capacity-aware candidate review to reduce LLM calls
- [x] dedicated research timeout/retry/token configuration
- [x] nested TDX F10 sector parser + null-cache refresh
- [x] sector parser diagnostics in research preflight
- [x] additional automated regression tests

---

## v0.6.1 Gemini 接力测试交付补充

新增文档/脚本：

- `CHANGELOG_V0.6.1.md`：逐项修复记录、旧问题、代码语义、验收门槛；
- `GEMINI_CONTINUATION_TEST_PLAN.md`：给 Gemini/其他 LLM 的标准接力测试协议；
- `scripts/run_gemini_validation.sh`：默认跑 2026-07-01 ~ 2026-09-30 的 preflight + deterministic A/B + LLM A/B；
- `TESTING_GUIDE.md`：版本与接力入口更新为 v0.6.1；
- `README.md`：加入接力测试入口和反馈文件位置。

接力测试硬规则：

1. 复测前重置旧 LLM research cache；
2. LLM API/schema error 不得解释为 REJECT；
3. LLM A/B 只有 `failures=0`、`error_candidates=0`、`llm_experiment_valid=true` 才有效；
4. Sector mapping coverage 为 0 时不得声明 sector router 已验证；
5. 不允许根据当前诊断数据直接把生产仓位提高到 30%~40%/80%~100%；
6. 下一轮优先比较 `no_triple_golden_cross` vs `llm_gate_no_triple`。
