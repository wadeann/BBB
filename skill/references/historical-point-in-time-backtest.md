# Historical Point-in-Time Backtest Extension

正式全市场回测必须按每个历史交易日重建当时真实股票池，不得使用今天的存量股票列表替代过去的 Universe。

详细实施说明见项目根目录：

- `LLM_NEXT_STEPS.md`
- `MCP_HISTORICAL_DATA_CONTRACT.md`
- `BACKTEST_DATA_QUALITY.md`

核心要求：

1. 每个历史日期单独构建 Universe。
2. 历史 ST、停牌、退市整理、上市/退市状态按当时有效记录处理。
3. strict 模式不得 silent fallback 到 current-universe。
4. 板块归属优先使用 point-in-time membership。
5. report.json 必须披露 survivorship bias、覆盖率和 PIT 状态。
6. 后续新增 HistoricalUniverseProvider，不要把临时逻辑硬塞进 BacktestEngine。
