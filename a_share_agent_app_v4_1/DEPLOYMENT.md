# A股 Agent v0.4.1 部署指南

本文件面向 Linux 单机第一阶段生产部署。生产仍建议保持 `mode: paper` 与 `allow_real_execution: false`。

## 1. 目录与用户

```bash
sudo useradd --system --home /opt/a-share-agent --shell /usr/sbin/nologin a-share || true
sudo mkdir -p /opt/a-share-agent
sudo chown -R a-share:a-share /opt/a-share-agent
```

将发布包解压到 `/opt/a-share-agent`。

## 2. Python

```bash
cd /opt/a-share-agent
sudo -u a-share python3 -m venv .venv
sudo -u a-share .venv/bin/python -m pip install --upgrade pip
sudo -u a-share .venv/bin/python -m pip install -e .
sudo -u a-share mkdir -p data/audit
```

## 3. 项目内 `.env`

发布包根目录已经包含空白 `.env` 和 `.env.example`。应用会自动加载 `/opt/a-share-agent/.env`，无需放到 `/etc`，也无需手工 `source`。

```bash
cd /opt/a-share-agent
sudo chown a-share:a-share .env
sudo chmod 600 .env
sudo -u a-share editor .env
```

系统已有环境变量优先于 `.env`。`.env` 不进行 shell 变量展开，因此密码中的 `$` 会按字面值保留。

必填：

```text
ASHARE_MCP_USERNAME
ASHARE_MCP_PASSWORD
ASHARE_LLM_BASE_URL
ASHARE_LLM_API_KEY
ASHARE_LLM_MODEL
```

可选通知：

```text
ASHARE_FEISHU_WEBHOOK_URL
ASHARE_FEISHU_SECRET
```

不要把 `.htpasswd` 中的 APR1 哈希误当成 Basic Auth 明文密码，除非服务器明确要求如此。

## 4. Preflight

```bash
cd /opt/a-share-agent
.venv/bin/a-share-agent --root /opt/a-share-agent mcp-probe
.venv/bin/a-share-agent --root /opt/a-share-agent llm-probe
.venv/bin/a-share-agent --root /opt/a-share-agent preflight
```

先解决 `missing_tools`、认证、协议或 LLM JSON 输出问题，再启动 Worker。

每个探针都会自动保存脱敏 JSON 报告：

```text
/opt/a-share-agent/data/diagnostics/latest_mcp_probe.json
/opt/a-share-agent/data/diagnostics/latest_llm_probe.json
/opt/a-share-agent/data/diagnostics/latest_notification_probe.json
/opt/a-share-agent/data/diagnostics/latest_preflight.json
```

普通运行日志写入 `/opt/a-share-agent/data/logs/`。交易事实仍写入 `data/audit/`。

## 4.1 飞书通知（可选）

先在目标飞书群创建自定义机器人，取得 Webhook；如果开启签名校验，再取得签名密钥。不要把它们写入 `runtime.yaml`。

编辑 `config/runtime.yaml`：

```yaml
notifications:
  feishu:
    enabled: true
```

然后在项目根目录 `.env` 中设置：

```text
ASHARE_FEISHU_WEBHOOK_URL=...
ASHARE_FEISHU_SECRET=...
```

部署前发送无交易含义的测试消息：

```bash
.venv/bin/a-share-agent --root /opt/a-share-agent notification-probe --channel feishu
```

查看持久化发送状态：

```bash
.venv/bin/a-share-agent --root /opt/a-share-agent notification-status --limit 50
```

飞书失败不会让交易 Worker 崩溃；失败投递会进入 `notification_state.sqlite` 并按退避策略重试。

## 5. systemd：Worker + Web

```bash
sudo cp deploy/a-share-agent-worker.service.example /etc/systemd/system/a-share-agent-worker.service
sudo cp deploy/a-share-agent-web.service.example /etc/systemd/system/a-share-agent-web.service
sudo cp deploy/a-share-agent.target.example /etc/systemd/system/a-share-agent.target
sudo systemctl daemon-reload
sudo systemctl enable --now a-share-agent.target
```

检查：

```bash
systemctl status a-share-agent-worker
systemctl status a-share-agent-web
journalctl -u a-share-agent-worker -f
```

Worker 必须保持独立运行；浏览器关闭不应影响它。

## 6. Web / Nginx

Uvicorn 只监听 `127.0.0.1:8000`。不要直接公网开放。

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/a-share-agent
sudo ln -s /etc/nginx/sites-available/a-share-agent /etc/nginx/sites-enabled/a-share-agent
sudo nginx -t
sudo systemctl reload nginx
```

公网使用时在 Nginx/LB 层加入 HTTPS 和身份认证。SSE 路径必须关闭 buffering。

## 7. 健康检查

```bash
curl -fsS http://127.0.0.1:8000/api/worker/status
curl -fsS http://127.0.0.1:8000/api/dashboard
```

浏览器应看到 Worker heartbeat 和 LIVE SSE 状态。

## 8. 数据持久化与备份

必须持久化：

```text
data/audit/audit_index.sqlite
data/audit/intent_store.sqlite
data/audit/worker_state.sqlite
data/audit/notification_state.sqlite
data/audit/YYYY/MM/DD/events.jsonl
data/audit/YYYY/MM/DD/snapshots/
data/diagnostics/
data/logs/
config/
```

备份示例：

```bash
sudo bash deploy/backup.sh.example /opt/a-share-agent /var/backups/a-share-agent
```

严格点时间备份建议先停止 Worker/Web，或后续使用 SQLite online backup。`.env` 含敏感凭证，不进入默认 backup.sh；如需备份请单独加密保存。

## 9. 重启恢复验证

```bash
sudo systemctl restart a-share-agent-worker
```

检查：

```bash
.venv/bin/a-share-agent --root /opt/a-share-agent --backend production worker-status
```

Worker 启动会先做 `RECOVERY_SYNC`；同一 `(trade_date, phase, slot)` 不应重复执行。

## 10. 升级

```bash
sudo systemctl stop a-share-agent.target
sudo bash deploy/backup.sh.example /opt/a-share-agent /var/backups/a-share-agent
# 更新代码
cd /opt/a-share-agent
.venv/bin/python -m pip install -e .
PYTHONPATH=. .venv/bin/pytest -q
# 再做 preflight
sudo systemctl start a-share-agent.target
```

升级后重点验证 Audit Hash Chain、Worker lease、phase slot、SSE 和 MCP catalog。

## 11. 回滚

保留发布版本/tag 和对应 config。停止服务、恢复上一版本代码与 `data/config` 备份，随后：

```bash
.venv/bin/a-share-agent --root /opt/a-share-agent verify-audit --date YYYY-MM-DD
```

Hash Chain 无异常后再启动。
