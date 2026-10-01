# Backtest Lab — 历史回测规范

## 目标

验证 Strategy Router、确定性信号、组合风险和执行假设在历史数据上的表现。回测是研究工具，不调用 Risk/Exec 下单接口，也不把研究结果自动提升为生产参数。

## 防未来函数规则

- 日线形态只在当日收盘后确认。
- 由收盘信号产生的买入/卖出，最早下一交易日开盘成交。
- 当日买入的 A 股仓位不可当日卖出（T+1）。
- 不使用当前 market_health/mainline/hot_signals 去解释历史日期。
- 历史大盘环境由宽基历史价格重建；板块环境优先由板块历史价格重建。
- 财务/公告若没有 `publish_time/available_at`，不得作为历史时点输入。

## 成交模型

默认模拟：佣金、最低佣金、卖出印花税、滑点、100股买入单位、涨跌停一字板无法成交、保护止损、最大持仓天数和组合仓位限制。参数见 `config/backtest.yaml`。

## 回测输出

每个 run 保存：

- `report.json`：机器可读完整结果，后续策略改进的首选输入。
- `report.html`：人工阅读报告。
- `trades.csv`：逐笔成交与 MAE/MFE。
- `equity_curve.csv`：每日权益。
- `monthly_returns.csv`：月收益。
- `rejections.csv`：信号被 Router/阈值/风控/成交约束拒绝的原因。
- `walk_forward.json`：启用 Walk-Forward 时生成。

## 数据偏差

如果使用“当前股票池”回测过去，会有幸存者偏差。报告必须标记。严谨测试应提供 point-in-time 股票池或至少保存每个历史日期的可交易证券集合。当前 F10 行业归属也可能与历史行业归属不同，必须在报告中披露。

## Walk-Forward

当前实现使用训练窗口选择 `min_score`，再在紧邻的未见测试窗口验证。训练集与测试集不重叠。Walk-Forward 结果只能提出 Change Proposal，不能自动改生产配置。
