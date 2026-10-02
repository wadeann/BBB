# A股 Agent Runtime + Web Workbench v0.7.0

> Gemini 接力测试优先阅读 `GEMINI_HANDOFF_V07.md`，完成后按 `VALIDATION_REPORT_TEMPLATE.md` 输出。

## v0.7 重点：全市场动态历史选股回测

v0.7 不再把固定 `universe.txt` 当作正式两年回测股票池。正式 Research 使用每个历史交易日的 Point-in-Time 全A股 universe，结合当时证券状态、历史行业归属和行业历史走势重新筛选。

正式验证请先阅读：

- `FULL_MARKET_BACKTEST.md`
- `GEMINI_FULL_MARKET_TEST_PLAN.md`
- `MCP_HISTORICAL_DATA_CONTRACT.md`
- `CHANGELOG_V0.7.md`


### Gemini 分阶段测试脚本

不要一次性盲跑所有阶段。使用：

```bash
PHASE=test      bash scripts/run_gemini_v07_validation.sh
PHASE=mcp       bash scripts/run_gemini_v07_validation.sh
PHASE=preflight bash scripts/run_gemini_v07_validation.sh
PHASE=smoke     bash scripts/run_gemini_v07_validation.sh
PHASE=perf      bash scripts/run_gemini_v07_validation.sh
PHASE=full3m    bash scripts/run_gemini_v07_validation.sh
PHASE=llm3m     bash scripts/run_gemini_v07_validation.sh
PHASE=full2y    bash scripts/run_gemini_v07_validation.sh
PHASE=collect   bash scripts/run_gemini_v07_validation.sh
```

`full2y` 只能在 `preflight + full3m` 达到 `RESEARCH_GRADE` 后执行。
测试结束后 `PHASE=collect` 会打包回传材料。

一键三个月全市场验证：

```bash
bash scripts/run_full_market_validation.sh
```

正式收益测试必须使用 `--universe-mode strict_point_in_time` 且不设置 `--max-universe`。`max_universe=50/500` 只用于接口和性能冒烟测试。

Gemini commit `e2d700a` 的 JSON Schema / reasoning-token 兼容修改已经合并，同时保留 LLM ERROR != REJECT、Schema 强校验和 compact historical features。


v0.4 把系统从“Web 页面驱动的 Runtime”升级成**独立后台 Worker + 只读 Web Workbench**，并加入生产 MCP HTTP Adapter、OpenAI-compatible LLM Client、SSE 实时事件流、可靠消息通知和部署前探针。

> 当前交易模式仍默认为 `paper`，`allow_real_execution=false`。根据现有接口说明，`mcp_exec_place_order` 是模拟交易接口；不要把它当作真实券商实盘接口。

## v0.4 架构

```text
                     ┌──────────────────────────┐
                     │  a-share-agent worker    │
                     │                          │
                     │ Scheduler / Recovery     │
                     │ Market / Sector          │
                     │ Strategy / LLM           │
                     │ Risk / Intent / Exec     │
                     │ Audit / Daily Review     │
                     └────────────┬─────────────┘
                                  │
                       Audit + SQLite + Snapshot
                                  │
                     ┌────────────▼─────────────┐
                     │   a-share-agent web      │
                     │   只读查询 / Replay       │
                     │   SSE Audit Event Stream │
                     └────────────┬─────────────┘
                                  │
                               Browser
```

关闭浏览器、刷新页面或重启 Web，都不会停止 Worker。Worker 使用 SQLite lease 防止同一个项目目录同时启动两个自主调度器；阶段任务使用持久化 slot 去重，重启不会重复执行同一时间槽。

## 已配置的生产 MCP Endpoint

`config/runtime.yaml` 已加入：

```text
intel  https://mcp.salahe.us.ci/api/intel
risk   https://mcp.salahe.us.ci/api/risk
exec   https://mcp.salahe.us.ci/api/exec
```

三者默认使用 HTTP Basic Auth，但**用户名和密码不写入 YAML/代码/Audit**，只从环境变量读取：

```text
ASHARE_MCP_USERNAME
ASHARE_MCP_PASSWORD
```

如果网关使用 Apache htpasswd，`ASHARE_MCP_PASSWORD` 通常应填写实际 Basic-Auth 明文密码，而不是 `.htpasswd` 中的 APR1 哈希；除非你的网关明确把哈希字符串本身当密码。

Jin10 Endpoint 目前没有提供，因此默认 `enabled: false`。

## OpenAI-compatible LLM

Worker 支持兼容 OpenAI Chat Completions 的接口：

```text
ASHARE_LLM_BASE_URL
ASHARE_LLM_API_KEY
ASHARE_LLM_MODEL
```

默认路径：

```text
/v1/chat/completions
```

支持：

- `json_schema` structured output；
- 不兼容时自动降级 `json_object`；
- 超时与重试；
- JSON 提取和 Schema 校验；
- API Key 不进入错误日志/Audit。

Web 进程**不会初始化 LLM Client**。只有 Worker 持有 LLM 凭证。

## 安装

推荐 Python 3.11+：

```bash
cd a_share_agent_app_v7
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

测试：

```bash
PYTHONPATH=. pytest -q
```

## Fake/Paper 演示

终端 A：

```bash
a-share-agent --root "$PWD" --backend fake worker
```

终端 B：

```bash
a-share-agent --root "$PWD" --backend fake web \
  --seed-demo --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/docs
```

## 生产配置

复制环境模板：

```bash
cp .env.example .env  # 发布包已自带空白 .env 时可直接编辑
chmod 600 .env
```

编辑：

```bash
editor .env
```

内容示例：

```text
ASHARE_MCP_USERNAME=your_username
ASHARE_MCP_PASSWORD=your_actual_basic_auth_password

ASHARE_LLM_BASE_URL=https://your-llm.example.com
ASHARE_LLM_API_KEY=replace_me
ASHARE_LLM_MODEL=your-model-name
```

发布包内的 `.env` 只包含空白占位符；部署后直接编辑项目根目录 `.env`。不要把填入真实凭证后的 `.env` 提交到 Git 或再次分发。

### 部署前探针

程序会自动读取项目根目录 `.env`，先独立测试 MCP：

```bash
a-share-agent --root "$PWD" mcp-probe
```

`mcp-probe` 会：

1. 对 intel/risk/exec 做 MCP `initialize`；
2. 执行 `tools/list`；
3. 检查 session/protocol；
4. 将服务器实际工具名与本项目 catalog 对比；
5. 输出 `missing_tools` / `unexpected_tools`。

它**不要求 LLM 已配置**。

单独测试 LLM：

```bash
a-share-agent --root "$PWD" llm-probe
```

一次完成全部生产前检查：

```bash
a-share-agent --root "$PWD" preflight
```

只有 MCP catalog 与 LLM probe 都通过时，`preflight` 才返回成功退出码。

## 项目内 `.env` 与自动加载

程序启动时会自动读取 `PROJECT_ROOT/.env`，无需手工 `source`，也不需要 `/etc/a-share-agent/`。

优先级：

```text
进程/系统环境变量 > PROJECT_ROOT/.env > 未配置
```

因此临时通过系统环境变量覆盖某个值时，不会被 `.env` 覆盖。`.env` 解析不做 shell 展开，密码中的 `$` 会按字面值保留。

发布包包含：

```text
.env          # 空白模板，可直接编辑
.env.example  # 同内容参考模板
```

部署后：

```bash
cd /opt/a-share-agent
chmod 600 .env
editor .env
```

## 诊断报告与运行日志

以下命令除了打印终端结果，还会自动写入 `data/diagnostics/`：

```bash
a-share-agent --root "$PWD" mcp-probe
a-share-agent --root "$PWD" llm-probe
a-share-agent --root "$PWD" notification-probe --channel feishu
a-share-agent --root "$PWD" preflight
```

例如：

```text
data/diagnostics/
├── mcp_probe_YYYYMMDD_HHMMSS_xxxxxx.json
├── latest_mcp_probe.json
├── llm_probe_YYYYMMDD_HHMMSS_xxxxxx.json
├── latest_llm_probe.json
├── notification_probe_YYYYMMDD_HHMMSS_xxxxxx.json
├── latest_notification_probe.json
├── preflight_YYYYMMDD_HHMMSS_xxxxxx.json
└── latest_preflight.json
```

报告写盘前会自动脱敏 `password/api_key/token/secret/webhook` 等字段。部署测试后，优先提供 `data/diagnostics/latest_preflight.json` 进行排查。

普通运行日志写入：

```text
data/logs/
├── worker.log
├── web.log
├── mcp-probe.log
├── llm-probe.log
├── preflight.log
├── notification-probe.log
└── error.log
```

交易事实仍只写 `data/audit/`；diagnostics/logs 不替代 Audit Log。

## Worker 调度

Worker 使用 `Asia/Shanghai` 业务时间，并读取 `config/schedule.yaml`。

关键行为：

- 每个交易日调用 `mcp_intel_is_trading_day()`，结果按日缓存；
- 非交易日返回 `NON_TRADING_DAY`，不执行阶段任务；
- 交易日接口异常时 fail-closed，返回 `CALENDAR_ERROR/DEGRADED`；
- SQLite lease 保证一个项目目录只有一个主动 Worker；
- `(trade_date, phase, slot)` 唯一约束防重复运行；
- Worker 启动先执行 `RECOVERY_SYNC`；
- 盘中 interval phase 根据配置定时刷新；
- UI 不驱动 Scheduler。

查看 Worker 状态：

```bash
a-share-agent --root "$PWD" --backend production worker-status
```

测试单次 tick：

```bash
a-share-agent --root "$PWD" --backend production worker --once
```

## SSE 实时事件

Web 提供：

```text
GET /api/events/stream
```

Worker 写 AuditIndex 后，Web 从 SQLite 增量读取事件并通过 SSE 推送浏览器。支持 `Last-Event-ID` 续传，因此 Worker/Web 分离后不依赖内存 Event Bus。

SSE 推送只包含 Audit 事件元数据；详情仍通过只读 API 查询。

## 消息通知：浏览器弹窗 + 飞书

v0.4 的通知机制直接订阅 **Audit Event**，不允许策略模块绕开审计链单独发消息。默认支持：

- 浏览器站内 Toast；
- 浏览器原生桌面通知（用户主动授权后；通常要求 HTTPS 或 localhost）；
- 飞书自定义机器人 Webhook；
- 飞书可选签名密钥；
- `event_id + channel` 持久化去重；
- 失败退避重试；
- Worker 重启后继续处理尚未成功发送的通知。

默认触发：

```text
SIGNAL_DECISION     高质量 ENTRY/EXIT 信号预警
RISK_RESULT         默认只提醒 REJECT/异常
ORDER_PROPOSAL      实盘提案提醒
ORDER_REQUEST       模拟/执行请求提醒
EXECUTION_RECEIPT   成交/委托回执
AUDIT_GAP           审计链告警
```

通知格式会区分“信号”“交易提案/请求”“真实执行回执”，避免把候选信号误读成已经成交。

### 飞书配置

`config/runtime.yaml` 默认关闭飞书外发：

```yaml
notifications:
  feishu:
    enabled: false
    webhook_url_env: ASHARE_FEISHU_WEBHOOK_URL
    secret_env: ASHARE_FEISHU_SECRET
    format: interactive
```

启用时，把 `enabled` 改为 `true`，并在项目根目录 `.env` 中填写：

```text
ASHARE_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
ASHARE_FEISHU_SECRET=...   # 如果机器人开启签名校验
```

测试通知：

```bash
a-share-agent --root "$PWD" notification-probe --channel feishu
```

查看最近发送/重试状态：

```bash
a-share-agent --root "$PWD" notification-status --limit 50
```

持久化状态：

```text
data/audit/notification_state.sqlite
```

Webhook、签名密钥和其他认证值不会写进 Audit、README 或 ZIP。

## Web 工作台

已有：

- 大盘 Regime / Trend / Sentiment；
- 热门板块、5/20日趋势和 Strategy Router；
- 连板梯队和连板股票；
- 热点异动；
- 候选股 / 持仓 / PnL；
- Worker heartbeat；
- SSE Live 状态；
- 板块详情；
- 个股详情；
- Audit Timeline；
- Trace Drill-down；
- Exact Replay；
- DAILY_REVIEW。

Web 默认：

```yaml
web:
  allow_phase_control: false
  expose_order_actions: false
```

没有直接 `/buy` 或 `/sell` API。

## systemd 生产部署

推荐使用两个独立 Service：

```text
deploy/a-share-agent-worker.service.example
deploy/a-share-agent-web.service.example
```

可选 target：

```text
deploy/a-share-agent.target.example
```

安装：

```bash
sudo cp deploy/a-share-agent-worker.service.example /etc/systemd/system/a-share-agent-worker.service
sudo cp deploy/a-share-agent-web.service.example /etc/systemd/system/a-share-agent-web.service
sudo cp deploy/a-share-agent.target.example /etc/systemd/system/a-share-agent.target
sudo systemctl daemon-reload
sudo systemctl enable --now a-share-agent.target
```

状态：

```bash
systemctl status a-share-agent-worker
systemctl status a-share-agent-web
journalctl -u a-share-agent-worker -f
journalctl -u a-share-agent-web -f
```

详细步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## Nginx

`deploy/nginx.conf.example` 已对 `/api/events/stream` 关闭代理缓冲，并增加长连接 read timeout。SSE 路径不能使用默认 buffering，否则实时事件会被批量延迟发送。

## 数据与日志

默认：

```text
data/audit/
├── audit_index.sqlite
├── intent_store.sqlite
├── worker_state.sqlite
├── notification_state.sqlite
└── YYYY/MM/DD/
    ├── events.jsonl
    ├── manifest.json
    └── snapshots/
```

Audit 仍采用 append-only + SHA-256 hash chain。Worker 状态库单独保存 lease、heartbeat 和 phase slot。

验证历史链：

```bash
a-share-agent --root "$PWD" verify-audit --date YYYY-MM-DD
```

## 安全边界

生产配置默认：

```yaml
mode: paper
safety:
  allow_real_execution: false
  require_audit_write_before_side_effect: true
  reject_on_audit_failure: true
  reject_on_schema_failure: true
```

即使真实 MCP 已连接，也建议先连续 paper/shadow 验证。任何未来真实券商 Adapter 仍必须经过：

```text
Signal → Phase Permission → Local Risk → Risk MCP
       → Approved Intent → Execution Engine → Broker Adapter
```

LLM 无权覆盖 Risk REJECT。

## v0.4.1 验收状态

```text
Worker/Web separation       PASS
Persistent worker lease     PASS
Phase slot idempotency      PASS
Trading-day fail-closed     PASS
Cross-process Audit SSE     PASS
Production MCP adapter      PASS (MockTransport contract test)
Basic/Bearer/Header auth    implemented
MCP catalog validation      implemented
OpenAI-compatible LLM       PASS (MockTransport contract test)
Browser popup notification  PASS
Feishu webhook/signature    PASS (MockTransport contract test)
Notification dedup/retry    PASS
Isolated MCP/LLM probes     implemented
Audit hash chain            PASS
Existing Web/Replay         PASS
pytest                      23 passed
```

真实 MCP 的最后一步验证需要在部署机器上使用**实际 Basic Auth 明文密码**执行 `mcp-probe/preflight`。真实 LLM 需要你填 `base_url/api_key/model` 后执行 `llm-probe`。


---

# v0.5 Backtest Lab：完整历史回测

v0.5 新增研究级历史回测引擎，目的不是“把历史收益做漂亮”，而是用固定、可复现的规则验证过去两年的真实策略表现。

## 核心约束

- 日线信号在收盘后确认，**最早下一交易日开盘成交**，不允许用信号日收盘价穿越成交。
- A股 T+1：当日新买仓位不可当日卖出。
- 模拟佣金、最低佣金、卖出印花税、滑点、100股买入单位、涨跌停一字板无法成交。
- 复用 `DeterministicSignalEngine + StrategyRouter + 风险仓位参数`。
- 回测不调用 `mcp_exec_*`，也不调用 Risk/Exec 执行副作用。
- 为保证可复现，批量历史回测默认**不调用 LLM**；报告会明确 `llm_used=false`。LLM 负责生产中的综合解释/反证，而历史大批量研究采用确定性策略核心。
- 历史 market regime 由沪深300/中证1000/创业板等宽基历史K线重建；不会拿今天的 `market_health/mainline/hot_signals` 回填过去。
- 板块强弱优先由 `sector_history` 的历史价格计算；缺少板块历史时按配置降级并在 `data_quality` 中披露。

## 默认两年测试

配置文件：`config/backtest.yaml`

默认区间：

```text
2024-10-01 → 2026-09-30
```

默认初始资金 100 万、单笔风险 0.5%、单票上限 10%、总仓位上限 50%、最低评分 75、5 bps 滑点。所有值都可改，但修改后报告会保存完整 settings。

### 股票池

最推荐在：

```text
data/backtest/universe.txt
```

每行写一个代码，例如：

```text
600000.SH
000001.SZ
300750.SZ
```

如果文件为空且使用 production backend，系统会分页调用问财构造**当前股票池**。这能运行历史测试，但存在幸存者偏差；`report.json` 会自动标记 `survivorship_bias=true`。若要做严谨论文级验证，应提供历史 point-in-time 股票池。

默认 `max_universe=500`，用于第一轮部署验证和控制内存。确认服务器资源与 MCP 历史数据覆盖正常后，可以在 `config/backtest.yaml` 将其调大或设为 `0`。

## 命令行运行

使用真实 intel MCP（不需要 LLM key，也不会调用 exec）：

```bash
a-share-agent --root "$PWD" --backend production backtest \
  --start 2024-10-01 \
  --end 2026-09-30
```

显式股票：

```bash
a-share-agent --root "$PWD" --backend production backtest \
  --start 2024-10-01 --end 2026-09-30 \
  --symbol 600000.SH \
  --symbol 000001.SZ
```

同时执行 Walk-Forward：

```bash
a-share-agent --root "$PWD" --backend production backtest \
  --start 2024-10-01 --end 2026-09-30 \
  --walk-forward --train-months 12 --test-months 3
```

查看历史 run：

```bash
a-share-agent --root "$PWD" backtest-list
```

## Web Backtest Lab

Web 顶部新增“回测”标签页，可以填写日期、资金、评分阈值、股票数量上限、滑点，并启动研究任务。任务在独立线程执行，浏览器轮询 job 状态；这条路径没有 Risk/Exec 下单能力。

## 每次回测的输出

```text
data/backtest/runs/<run_id>/
├── report.json
├── report.html
├── trades.csv
├── equity_curve.csv
├── monthly_returns.csv
├── rejections.csv
└── walk_forward.json       # 如启用
```

**后续请优先把 `report.json` 提供给我。** 它包含：总收益、CAGR、最大回撤、Sharpe/Sortino/Calmar、胜率、Profit Factor、期望收益、MAE/MFE、交易成本、每月收益、达到月收益30%的月份数量与比例、沪深300同期收益、策略/Router/市场状态/板块强弱分组结果、逐笔成交、所有拒绝原因和数据质量说明。

## 如何解释“过去两年赚钱能力”

报告应至少同时看：

1. 总收益与同期基准超额；
2. 最大回撤及回撤持续时间；
3. Profit Factor / Expectancy，而不只是胜率；
4. 每月收益分布，尤其是达到30%的月份比例，而不是强迫系统每月达到30%；
5. `risk_on / neutral / risk_off` 中各策略表现；
6. 强/中/弱板块中的策略表现；
7. 交易费用和滑点敏感性；
8. Walk-Forward 未见样本表现；
9. `data_quality` 与幸存者偏差警告。

回测结果不能自动修改生产参数。发现问题后应生成 Change Proposal，再经过回测、Walk-Forward/OOS、Paper Shadow 后才能晋级。

---

# v0.5.1：给后续 LLM / 开发者的 Point-in-Time 回测实施说明

本包新增三份必须优先阅读的交接文档：

- `LLM_NEXT_STEPS.md`：后续 LLM/开发者实施任务清单、建议代码位置、验收标准。
- `MCP_HISTORICAL_DATA_CONTRACT.md`：建议新增的 Intel MCP 历史证券主表/历史板块归属接口 Contract。
- `BACKTEST_DATA_QUALITY.md`：回测数据质量、幸存者偏差、Point-in-Time、覆盖率与研究等级规范。

另外 Skill 中增加：

- `skill/references/historical-point-in-time-backtest.md`
- `skill/references/mcp-historical-interface-proposal.md`

## 重要说明

当前 v0.5 的 `HistoricalDataProvider.load_universe()` 在没有历史股票池文件时，会使用当前问财结果作为历史回测股票池，并在报告中标记 survivorship bias。这条路径只适合开发/诊断，不应作为最终“过去两年赚钱能力”的严格结论。

正式版目标必须是：

```text
历史日期 D
→ 系统自动获得 D 当日真实全A股 Universe
→ 当日状态硬过滤
→ Market/Sector/Strategy
→ Signal/Risk
→ 次日成交
→ Portfolio
→ Report
```

用户不应手工指定正式全市场回测股票名单。

建议后续 LLM 优先实现独立 `HistoricalUniverseProvider`，并增加 strict point-in-time 模式。详细接口和验收测试请见上述三份文档。


---

# v0.6 Research Lab：标准化 A/B、LLM Gate 与反馈包

v0.6 在 v0.5 Backtest Lab 基础上新增研究套件，目标是把“为什么 -0.81%”“LLM 是否有增量价值”“板块 Router 到底有没有贡献”变成可复现的 A/B 实验，而不是凭感觉修改策略。

## 新增命令

```bash
a-share-agent --root "$PWD" --backend production research-preflight \
  --start 2024-10-01 --end 2026-09-30 \
  --universe-mode prefer_point_in_time --sample-size 30

a-share-agent --root "$PWD" --backend production research-suite \
  --start 2024-10-01 --end 2026-09-30 \
  --experiment baseline \
  --experiment no_triple_golden_cross \
  --experiment core_signal_focus \
  --experiment router_disabled \
  --experiment sector_disabled

a-share-agent --root "$PWD" research-latest
```

LLM A/B：

```bash
a-share-agent --root "$PWD" llm-probe

a-share-agent --root "$PWD" --backend production research-suite \
  --start 2024-10-01 --end 2026-09-30 \
  --include-llm \
  --experiment baseline \
  --experiment llm_gate_baseline \
  --experiment no_triple_golden_cross \
  --experiment llm_gate_no_triple
```

## 标准实验

配置：`config/research.yaml`

- `baseline`：当前确定性策略 + Router。
- `no_triple_golden_cross`：只移除三线金叉，测它的边际贡献。
- `core_signal_focus`：仅保留 `single_bull_hold + high_volume_breakout`，用于诊断，不自动晋级生产。
- `router_disabled`：关闭 Strategy Router 门控。
- `sector_disabled`：中性化板块上下文。
- `llm_gate_baseline`：在 baseline 候选上加历史 LLM PASS/WATCH/REJECT Gate。
- `llm_gate_no_triple`：去三线金叉后再加 LLM Gate。

## LLM 历史回测边界

LLM 只过滤已经通过确定性规则的候选，不能创造新信号、修改价格、修改 deterministic score 或直接下单。输入只包含当日及以前数据。

为降低模型参数记忆过去股票后续走势造成的隐性未来知识污染，默认：

```yaml
llm_filter_anonymize_symbol: true
```

LLM 不看到真实 ticker，只看到匿名证券 ID。输出会缓存到：

```text
data/backtest/llm_cache/
```

## Point-in-Time 股票池

v0.6 已加入可执行的历史股票池支持：

1. 优先读取 `data/backtest/security_master.csv`；
2. 或调用可选 Intel MCP `mcp_intel_get_historical_universe`；
3. `strict_point_in_time` 模式下缺少严格历史数据直接失败；
4. `prefer_point_in_time` 才允许降级到当前股票池，并标记幸存者偏差。

原始 53 个 MCP 工具契约保持不变；历史接口属于 optional research tools，缺少不会让普通生产 `mcp-probe` 失败。

## Gross / Cost / Net 分解

`report.json` 新增：

```text
gross_pnl_before_costs
round_trip_fees
estimated_slippage_cost
net_realized_pnl
gross_return_on_initial
net_realized_return_on_initial
```

用于区分“信号没有毛收益”与“毛收益被交易成本/滑点吃掉”。

## Research Validity

每个 Research Suite 实验报告新增：

```text
research_validity.grade = RESEARCH_GRADE | DIAGNOSTIC_ONLY
research_validity.reasons
```

股票覆盖过小、幸存者偏差、板块映射不是 Point-in-Time、板块几乎全为 `NEUTRAL_SECTOR` 等情况都会自动降级。

## 报告位置

单次 backtest：

```text
data/backtest/runs/<bt-run-id>/
```

标准 A/B suite：

```text
data/research/runs/<research-suite-id>/
├── research_summary.json
├── experiment_metrics.csv
├── FEEDBACK_README.md
└── feedback_bundle.zip
```

最近一次 suite：

```text
data/research/runs/latest.json
```

部署/数据质量预检：

```text
data/diagnostics/latest_research_preflight.json
```

反馈时优先提供：

1. `feedback_bundle.zip`
2. `latest_research_preflight.json`

完整测试步骤见 [TESTING_GUIDE.md](TESTING_GUIDE.md)。研究设计见 [RESEARCH_LAB.md](RESEARCH_LAB.md)。


## v0.6 最终验证状态

```text
pytest                         28 / 28 PASS
Python compile                 PASS
Research Suite CLI E2E         PASS
feedback_bundle.zip            PASS
Point-in-Time validity guard   PASS
LLM historical gate cache      PASS
Backtest no-exec safety        PASS
```

完整测试流程见 `TESTING_GUIDE.md`。
