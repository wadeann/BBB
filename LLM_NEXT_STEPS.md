# LLM / Developer Next Steps — Historical Point-in-Time Backtest

> 目的：把 v0.5 的历史回测从“可运行研究版”升级成严格的 **Point-in-Time 全市场回测**。
> 
> 本文是交给后续 LLM / 开发者的实施说明。不要把这里的建议当成已经完成的代码。

## 1. 当前状态

v0.5 已实现：

- 历史日线回测主链
- Strategy Router
- Deterministic Signal Engine
- A股 T+1
- 次日开盘成交
- 涨跌停成交限制
- 佣金 / 最低佣金 / 印花税 / 过户费 / 滑点
- 组合仓位与单笔风险
- Walk-Forward
- report.json / report.html / trades.csv / equity_curve.csv / monthly_returns.csv / rejections.csv

但当前正式回测还有一个重要缺口：

**如果没有用户提供历史股票池，`HistoricalDataProvider.load_universe()` 会调用当前问财结果构造“当前股票池”，然后拿这个股票池去回测历史日期。**

这会产生 Survivorship Bias（幸存者偏差）。因此当前 v0.5 的全市场回测只能称为“研究版”，不能称为严格 Point-in-Time 回测。

## 2. 正确目标

正式模式必须改成：

```text
历史日期 D
  ↓
HistoricalUniverseProvider.get_universe(D)
  ↓
获得 D 当天真实存在、可交易的全市场证券集合
  ↓
按 D 当时状态做硬过滤
  ↓
Market Regime
  ↓
Sector Regime / Historical Sector Membership
  ↓
Strategy Router
  ↓
Signal Engine
  ↓
Risk / Portfolio
  ↓
次日可成交性
  ↓
模拟交易
```

不能继续使用：

```text
今天的股票名单
  ↓
回测两年前
```

## 3. 需要新增的核心抽象

建议新增：

```python
class HistoricalUniverseProvider(Protocol):
    def get_universe(self, trade_date: str) -> HistoricalUniverseSnapshot:
        ...

    def get_security(self, symbol: str, as_of: str) -> HistoricalSecurityRecord | None:
        ...
```

建议数据模型：

```python
@dataclass
class HistoricalSecurityRecord:
    symbol: str
    name: str | None
    board: str | None
    listing_date: str | None
    delisting_date: str | None
    listed: bool
    risk_warning: bool | None
    suspended: bool | None
    delisting_period: bool | None
    industry_code: str | None
    industry_name: str | None
    tradable: bool
    status_source: str
    available_at: str | None

@dataclass
class HistoricalUniverseSnapshot:
    trade_date: str
    records: list[HistoricalSecurityRecord]
    source: str
    point_in_time: bool
    generated_at: str
    warnings: list[str]
```

## 4. 正式模式必须动态重建每日股票池

每天都要重新得到 Universe，不能固定一次：

```text
2024-10-08 → universe A
2024-10-09 → universe B
2024-10-10 → universe C
...
```

至少要正确处理：

- 当时已经上市
- 当时尚未退市
- 当日风险警示 / ST 状态
- 退市整理期
- 停牌
- 上市不足 N 个交易日
- 板块 / 市场类型
- 无行情或不可交易状态

## 5. 建议新增 Intel MCP 接口

优先建议 Intel 服务新增以下工具。名称可以调整，但语义不要改变。

### 5.1 `mcp_intel_get_historical_universe`

建议请求：

```json
{
  "date": "2024-10-08",
  "market": "A_SHARE",
  "include_suspended": true,
  "include_risk_warning": true,
  "include_delisting": true,
  "page": 1,
  "limit": 500
}
```

建议响应：

```json
{
  "trade_date": "2024-10-08",
  "point_in_time": true,
  "page": 1,
  "limit": 500,
  "total": 5342,
  "data": [
    {
      "symbol": "600000.SH",
      "name": "示例",
      "board": "SSE_MAIN",
      "listing_date": "1999-11-10",
      "delisting_date": null,
      "listed": true,
      "risk_warning": false,
      "suspended": false,
      "delisting_period": false,
      "industry_code": "BKxxxx",
      "industry_name": "银行",
      "tradable": true,
      "status_source": "historical_security_master",
      "available_at": "2024-10-08T09:00:00+08:00"
    }
  ]
}
```

### 5.2 `mcp_intel_get_historical_security`

建议请求：

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08"
}
```

建议响应与上面单条 `HistoricalSecurityRecord` 一致。

### 5.3 可选：`mcp_intel_get_historical_sector_membership`

建议请求：

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08"
}
```

建议响应：

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08",
  "point_in_time": true,
  "memberships": [
    {
      "type": "industry",
      "code": "BKxxxx",
      "name": "银行",
      "effective_from": "2023-01-01",
      "effective_to": null
    }
  ]
}
```

## 6. 接口质量要求

历史接口必须明确：

```text
point_in_time = true / false
available_at
source
```

如果历史状态是从今天状态回填或推断，不能伪装成 point-in-time 数据。

建议返回数据质量字段：

```json
{
  "data_quality": {
    "point_in_time": true,
    "coverage": 0.998,
    "missing_fields": [],
    "warnings": []
  }
}
```

## 7. Strict / Degraded 两种模式

建议新增配置：

```yaml
historical_universe:
  mode: strict_point_in_time   # strict_point_in_time | degraded_current_universe | file
  fail_on_non_point_in_time: true
  allow_current_universe_fallback: false
  min_coverage: 0.98

historical_sector_membership:
  mode: strict_or_neutral      # strict | strict_or_neutral | current_mapping
  fail_on_current_mapping: false
```

### strict_point_in_time

用于正式策略验证：

- 取不到历史 Universe → FAIL
- `point_in_time=false` → FAIL
- 覆盖率低于阈值 → FAIL 或标记不可发布报告
- 不允许静默 fallback 到今天股票池

### degraded_current_universe

只用于：

- 开发冒烟测试
- MCP 字段验证
- 性能测试

报告必须显著标记：

```json
{
  "research_grade": false,
  "survivorship_bias": true,
  "point_in_time_universe": false
}
```

## 8. 本地 Security Master 兜底方案

如果 Intel MCP 暂时不能提供历史证券主表，新增本地：

```text
data/reference/security_master.sqlite
```

建议表：

```sql
CREATE TABLE security_status_history (
    symbol TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    name TEXT,
    board TEXT,
    listing_date TEXT,
    delisting_date TEXT,
    risk_warning INTEGER,
    suspended INTEGER,
    delisting_period INTEGER,
    industry_code TEXT,
    industry_name TEXT,
    tradable INTEGER,
    source TEXT,
    available_at TEXT,
    PRIMARY KEY(symbol, effective_from)
);
```

查询历史时点：

```sql
SELECT *
FROM security_status_history
WHERE effective_from <= :date
  AND (effective_to IS NULL OR effective_to >= :date);
```

注意：不要只保存“最新状态”。必须保存有效期历史。

## 9. 板块必须避免时间穿越

当前 `sector_info()` 使用当前 F10 行业映射，因此存在历史归属偏差。

后续优先级：

1. 历史 sector membership
2. 历史行业代码有效期表
3. 如果只有当前映射：报告标记 `sector_membership_point_in_time=false`
4. strict 模式可将缺失板块上下文降级为 neutral，但必须记录覆盖率

## 10. 历史财务 / 新闻 / 公告要求

如果未来把这些加入回测：

- 必须有 `publish_time` / `available_at`
- 只能使用回测时点之前已经公开的数据
- 不能用后来修订后的最终值替代当时已知值而不标记

## 11. 报告必须增加的数据质量字段

建议 `report.json` 增加：

```json
{
  "data_quality": {
    "research_grade": true,
    "point_in_time_universe": true,
    "universe_coverage": 0.997,
    "sector_membership_point_in_time": true,
    "sector_coverage": 0.93,
    "price_coverage": 0.999,
    "financial_point_in_time": null,
    "survivorship_bias": false,
    "lookahead_detected": false,
    "warnings": []
  }
}
```

正式报告如果 `research_grade=false`，HTML 顶部应有醒目警告。

## 12. 应修改的代码位置

后续 LLM / 开发者重点检查：

```text
a_share_agent/backtest/data.py
  HistoricalDataProvider.load_universe()
  sector_info()

a_share_agent/backtest/models.py
  BacktestSettings

a_share_agent/backtest/service.py
  universe construction

a_share_agent/backtest/engine.py
  per-date universe membership / tradability

a_share_agent/backtest/report.py
  data-quality reporting

config/backtest.yaml
README.md
skill/references/backtesting.md
```

建议不要在 `engine.py` 临时写死过滤逻辑；Universe provider 应是独立组件。

## 13. 正式验收标准

新增自动测试至少覆盖：

1. 2026 年上市股票不能出现在 2024 Universe。
2. 2025 年已退市股票在退市前仍可出现在 Universe，退市后不能出现。
3. ST 状态按历史有效期变化。
4. 停牌日不可成交，但证券可以保留在 security master 中。
5. strict 模式拿不到 PIT Universe 必须失败。
6. degraded 模式可以继续，但报告必须 `research_grade=false`。
7. 当前股票池 fallback 不能在 strict 模式发生。
8. 历史 sector membership 不得读取未来生效记录。
9. Universe 响应分页完整性校验。
10. 同一日期重新回测得到相同 Universe hash。
11. 回测绝不调用 Risk/Exec 副作用 MCP。
12. 历史数据缓存必须包含 source + fetched_at + PIT 标识。

## 14. 推荐增加 Universe Hash

每天将 Universe canonical JSON 计算 SHA-256：

```text
universe_hash(date)
```

并写入报告 / backtest audit。

这样未来可以证明：

> 同一次回测使用的是哪一版历史股票池。

## 15. 不要做的事情

禁止：

- 用今天问财名单作为正式历史全市场验证而不提示
- 用当前 ST 状态回填过去
- 用当前行业归属假装历史行业归属
- 为了让回测跑完而静默跳过大量缺失股票
- 看到某只后来大涨的股票后再把它手工加入历史股票池
- 根据完整两年结果反复调参后，仍把同一两年称为“样本外验证”

## 16. 完成后的理想 CLI

```bash
a-share-agent --root "$PWD" --backend production backtest \
  --start 2024-10-01 \
  --end 2026-09-30 \
  --universe-mode strict-point-in-time \
  --walk-forward
```

用户不需要提供股票名单。

正式流程应当是：

```text
全历史市场
→ 系统自己重建每日 Universe
→ 系统自己筛选
→ 系统自己交易
→ 系统自己产出报告
```

这才是后续评估策略真实赚钱能力的基础。

---

# v0.6 状态更新（后续 LLM 必读）

v0.5.1 文档中以下事项已经在 v0.6 **部分或全部编码实现**：

- `HistoricalDataProvider.load_universe_for_period()` 已实现。
- 本地 `data/backtest/security_master.csv` Point-in-Time membership intervals 已实现。
- `strict_point_in_time` / `prefer_point_in_time` 已实现。
- `eligible_on(symbol, date)` 已接入历史 entry scan 和 pending BUY execution。
- optional `mcp_intel_get_historical_universe` 已接入 MCP mapping；服务端缺失时可降级或 strict fail。
- Research Validity / DIAGNOSTIC_ONLY 已实现。
- Standardized A/B Research Suite 已实现。
- Historical LLM candidate Gate 已实现，默认匿名 ticker，且不会调用实时数据源。

## 仍未完全解决

### 1. Historical sector membership

`mcp_intel_get_historical_sector_membership` 已作为 optional tool 名称被 Runtime 识别，但 Engine 尚未完整使用“按日期变化的行业/概念 membership intervals”重建每个交易日的 sector mapping。

当前若使用 `mcp_intel_tdx_f10` 获取行业映射，报告会：

```text
historical_sector_membership_point_in_time = false
```

并将 Research Validity 降级。

下一位开发者优先实现：

```text
HistoricalSectorMembershipProvider
symbol + date -> historical memberships
```

最好支持一次获取区间 intervals 并缓存，而不是 5000 股票 × 500 交易日逐日远程调用。

### 2. ST / suspension 状态区间的精细化

本地 `security_master.csv` 已支持 interval + tradable/st/suspended 字段，但正式全市场数据需要服务端给出状态历史区间，而不是只给上市/退市日期。

### 3. Dataset version / content hash

建议为 security master、price dataset、sector membership dataset 增加 dataset version + hash，并写入 report.json，便于不同回测机器完全复现。

### 4. LLM historical contamination

v0.6 已默认匿名 ticker，但模型仍可能通过行业、特殊K线形态等间接识别某些历史事件。因此 LLM A/B 应视为“降低污染后的实验”，不能宣称完全消除模型训练数据泄漏。

### 5. LLM 性价比

应通过 `llm_filter_stats.calls/cache_hits/failures` 和 A/B delta 同时评估收益改善与模型调用成本。不要只看收益。
