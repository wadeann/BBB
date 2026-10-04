# Proposed Intel MCP Historical Data Contract

> 状态：**接口建议，不是当前 53 个 MCP 工具的一部分。**
> 目的：支持严格 Point-in-Time A股历史回测。

## 推荐新增工具

### 1. `mcp_intel_get_historical_universe`

用途：按历史交易日返回当时全A股证券主表与交易状态。

#### Request

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

#### Response

```json
{
  "trade_date": "2024-10-08",
  "point_in_time": true,
  "source": "security_master_history",
  "page": 1,
  "limit": 500,
  "total": 5342,
  "data_quality": {
    "point_in_time": true,
    "coverage": 0.999,
    "missing_fields": [],
    "warnings": []
  },
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
      "tradable": true,
      "industry_code": "BK0001",
      "industry_name": "银行",
      "available_at": "2024-10-08T09:00:00+08:00"
    }
  ]
}
```

#### Contract

- `date` 是历史交易日。
- `total` 必须是该日期的完整证券记录数量，而不是当前证券数量。
- `point_in_time=true` 只有在状态确实来自当时有效记录时才能返回。
- 分页顺序必须稳定，建议按 `symbol` 升序。
- 相同请求应具有可重复结果；如底层历史库修订，应返回 `dataset_version`。

### 2. `mcp_intel_get_historical_security`

用途：获取单只证券在某一历史时点的状态。

#### Request

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08"
}
```

#### Response

同 `mcp_intel_get_historical_universe.data[]` 单条结构，并增加：

```json
{
  "point_in_time": true,
  "source": "security_master_history"
}
```

### 3. `mcp_intel_get_historical_sector_membership`

用途：获取证券在历史日期所属行业/概念。

#### Request

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08"
}
```

#### Response

```json
{
  "symbol": "600000.SH",
  "as_of": "2024-10-08",
  "point_in_time": true,
  "memberships": [
    {
      "type": "industry",
      "code": "BK0001",
      "name": "银行",
      "effective_from": "2023-01-01",
      "effective_to": null
    }
  ]
}
```

## 推荐错误码

```text
HISTORICAL_DATE_UNAVAILABLE
POINT_IN_TIME_NOT_SUPPORTED
PARTIAL_HISTORY
INVALID_DATE
INVALID_SYMBOL
PAGE_OUT_OF_RANGE
DATASET_NOT_READY
```

不要在不支持 Point-in-Time 时返回 HTTP 200 + 假数据。

## 推荐元数据

每个历史数据接口建议返回：

```json
{
  "dataset_version": "2026-10-02.1",
  "source": "...",
  "generated_at": "...",
  "point_in_time": true,
  "coverage": 0.999
}
```

## Backtest Runtime 对接口的预期

Runtime 应：

1. 分页拉完整 Universe。
2. 校验 `sum(items) == total`。
3. 计算 universe hash。
4. 缓存原始响应。
5. 报告 dataset_version。
6. strict 模式拒绝 `point_in_time=false`。
7. 不允许 silent fallback 到 current wencai universe。

## 兼容性

这些是 **建议新增接口**。现有 53 工具不要直接改变语义，避免破坏生产 Worker。

---

## v0.6 Runtime 接入状态

以下名称已经加入 Runtime 的 **optional Intel tools** 映射：

```text
mcp_intel_get_historical_universe
mcp_intel_get_historical_security
mcp_intel_get_historical_sector_membership
```

它们不属于原始 53 工具，因此：

- 服务端没有这些工具时，普通生产 MCP catalog probe 不失败；
- `mcp-probe` 会在 `optional_historical_tools` 中报告实际可用项；
- `mcp_intel_get_historical_universe` 已被 `HistoricalDataProvider.load_universe_for_period()` 尝试使用；
- `strict_point_in_time` 要求历史 universe interval 数据，否则回测拒绝开始；
- `prefer_point_in_time` 允许显式降级并记录 survivorship-bias warning。

`mcp_intel_get_historical_sector_membership` 当前仅完成工具映射，Engine 的按日期 sector membership interval 重建仍是下一阶段任务；因此当前 F10/current sector mapping 会导致 Research Validity 降级。


---

## v0.7 Runtime 已实现的调用策略

### Fast path：周期有效区间

为了两年全市场性能，Runtime **优先**尝试：

```json
{
  "start_date": "2024-10-01",
  "end_date": "2026-09-30",
  "mode": "membership_intervals",
  "include_status": true
}
```

建议响应返回每个证券状态有效期：

```json
{
  "point_in_time": true,
  "dataset_version": "...",
  "data_quality": {"coverage": 0.999},
  "data": [
    {
      "symbol": "600000.SH",
      "effective_from": "2024-01-01",
      "effective_to": null,
      "tradable": true,
      "risk_warning": false,
      "suspended": false,
      "delisting_period": false,
      "industry_code": "BK0001",
      "industry_name": "银行"
    }
  ]
}
```

如果服务端支持该模式，正式两年回测只需一次/少量 universe 拉取。

### Fallback：逐交易日分页

若 interval 模式不支持，Runtime 会按 benchmark 交易日调用本文前述 `date/page/limit` 接口，并缓存到 `data/backtest/cache/daily_universe/`。

### 强烈建议 universe 直接包含行业字段

全市场回测若每只股票每天单独查行业会非常慢。建议 `mcp_intel_get_historical_universe` 每条记录直接返回：

```text
industry_code
industry_name
```

如果只能通过 `mcp_intel_get_historical_sector_membership` 返回行业，推荐包含 `effective_from/effective_to`，Runtime 会把区间按 symbol 缓存。

### Strict 行为

`strict_point_in_time` 下：

- `point_in_time=false` -> FAIL
- 分页数量与 `total` 不一致 -> FAIL
- 无 historical universe -> FAIL
- 不允许退回当前问财股票池

这正是正式收益验证应使用的模式。

## Phase 2B 历史行情与物理证据门禁

- WF 内部区间为半开区间；`historical_bars` 的日期参数为闭区间。请求结束日取 exclusive end 前的最后一个真实交易日。
- warmup 起点来自交易日历与冻结 `warmup_bars`，不再以固定年份截断。规划日历、交易所覆盖证明和实际股票 warmup 必须分别通过。
- 信号行情显式请求 `qfq`；执行行情请求 `none`。未知 `provider_declared` 不得冒充 raw/none/qfq/hfq。qfq/hfq 必须具有匹配模式、逐日期有限正数 factors 与 PIT as-of 语义。
- adjusted/raw 股票 train、test、observation 区间及 benchmark 要覆盖每个要求日期；仅有明确历史 `SUSPENDED`/`DELISTED` 状态的股票日期可豁免可执行行情。缺 raw benchmark 或任一要求日期会阻断。
- 物理 provenance 使用单次调用证据，校验原始 JSON/SSE、JSON-RPC id、工具和参数、解析结果及响应内 metadata。可信 artifact root 来自 ledger，不来自响应；仅成功 HTTP 与完整绑定契约可标记 VERIFIED。
- Pup per-symbol coverage 使用 `requested_start`、`requested_end`、`complete`、`status`、`symbols`，并与 `bars`、`returned_rows` 的日期/数量核对；完整抓取不等于 PIT/version 已被证明。

2026-10-04 的真实只读冻结 WF 输出为 `data/backtest/phase2b_production_20261004T115407Z`：4 folds 均 `DATA_BLOCKED`，0 `RUN_FAILED`，0 completed；overall `FAILED` / `INSUFFICIENT_DATA`，physical provenance `UNVERIFIABLE`，没有 completion marker。前两个 folds 的 SZSE 覆盖证明开始于 2024-01-01，晚于所需 warmup；后两个 folds 的远端 catalog 缺少 `historical_bars`。

另一次真实 `tdx_kline(count=2000)` 抓取覆盖全部 10 个冻结标的与 `000300.SH`，每个返回 700 bars（2023-11-14..2026-09-30），冻结起点前仅 215 bars，少于要求的 260；响应缺 raw/factors/PIT/version 契约。这是已观测结果，不是已证明的全局上游上限。认证有效；这些阻塞不是缺账号。本地 Pup 修复尚未部署，policy 与真实执行继续关闭。
