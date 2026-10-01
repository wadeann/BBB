# A股 Agent Runtime + Web Workbench v0.4.1

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
cd a_share_agent_app_v4
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
pytest                      20 passed
```

真实 MCP 的最后一步验证需要在部署机器上使用**实际 Basic Auth 明文密码**执行 `mcp-probe/preflight`。真实 LLM 需要你填 `base_url/api_key/model` 后执行 `llm-probe`。
