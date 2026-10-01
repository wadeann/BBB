# Intel MCP Historical Interface Proposal

建议 Intel MCP 新增：

- `mcp_intel_get_historical_universe(date, market, page, limit, ...)`
- `mcp_intel_get_historical_security(symbol, as_of)`
- `mcp_intel_get_historical_sector_membership(symbol, as_of)`

这些工具当前不属于既有 53 个 MCP 工具，属于 v0.5.1 后续扩展建议。

完整请求/响应 Contract、错误码、数据质量字段见项目根目录 `MCP_HISTORICAL_DATA_CONTRACT.md`。
