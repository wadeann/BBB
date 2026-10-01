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
