# Implementation Status — v0.4.1

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
pytest: 20 passed
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
