# BBB Trading Decision System：产品目标与设计基线

记录日期：2026-10-03。来源：用户提供的《BBB Trading Decision System 产品设计、系统架构与开发路线图》1–64 节。本文件是该目标的结构化记录；开发任务与验收见 `BBB_DEVELOPMENT_PLAN.md`、`BBB_ACCEPTANCE_STANDARD.md`。本轮仅制定计划，开发由其他人执行，验收由本助手独立执行。

## 1. 产品终点

BBB 不是股票推荐机器人、万能选股策略、纯历史回测程序或 LLM 炒股系统。它是 deterministic trading engine + statistical validation + dynamic pattern routing + live execution + human-readable UI。

完整决策链：Historical/Live Data → Feature Engine → Market Regime → Theme/Sector Lifecycle → Strategy Router/Enabled Pattern Set → Pattern Engine → Candidate Ranking → Top Candidates → Risk/Context Review → Buy/Hold/Sell Decision → Alert/Intent/Execution → Audit/WebUI。

系统须回答：今天是什么市场、主线是什么、处于哪个生命周期、历史上适合哪些 Pattern、哪些股票真正触发、谁值得交易、何时/何价可以买、何时不能买、买后如何退出、相同环境历史表现如何；最终提供实时提醒、Paper Trading、有限自动交易。

优化单位：Market Regime × Theme Lifecycle × Pattern × Entry × Exit × Risk。目标是跨时间 OOS 稳定，不是历史收益峰值。

## 2. 五条最高设计原则

1. Historical 与 Live 使用同一套策略求值逻辑，不维护两套 detect/entry/exit。
2. 任一 T 时刻的决策只使用当时可见的信息；事件发生时间与信息可用时间必须区分。
3. Pattern 效果必须按市场环境、主线生命周期、Pattern ID 和版本拆分。
4. 历史效果以 Walk-Forward/OOS 为证据，不以 in-sample 或累计收益直接启用策略。
5. NO_TRADE 是正式、正确的结果；Top 10 是展示上限，不是配额。

## 3. 数据等级

### Trading Grade

用于策略开发、回测、Walk-Forward、盘中决策与 Paper Trading。最低要求：真实历史价格与成交量/额、正确时间和 Benchmark alignment、无未来数据、当时存在的股票、当日可交易状态、连续/正确复权序列、现实涨跌停及交易规则、可执行 Entry/Exit、合理成本。

Historical/Live 数据接口尽量统一；复权价用于适合的指标计算，raw 价用于执行与现金记账，不能混用。缺失关键输入不得生成虚假的可交易/完整结果。

### Research Grade

额外要求 PIT universe、ST/停牌、板块、corporate action provenance、官方日历/规则、source hashes、完整退市登记。此等级保留但不长期阻塞 Trading Grade；直接影响 Pattern correctness、lookahead、tradability 或 execution realism 的缺陷仍是阻断项。

`UNVERIFIED_CACHE` 只表示本地物理文件与哈希可复核，不证明上游已独立验证。不得提升标签或把哈希等同真实性。

## 4. Context 与 Pattern

Regime：BULL_TREND、BULL_VOLATILE、ROTATION、SIDEWAYS、BEAR、PANIC、RECOVERY，由 deterministic features 得出，不由 LLM 判断。逐步加入指数趋势/均线、Breadth、上涨下跌家数、新高新低、涨跌停、成交额及趋势、波动率、板块集中度、强势股持续性、风险偏好与相对强弱分布。缺失信息显式质量降级。

Lifecycle：EMERGING、ACCELERATING、LEADING、MATURE、DISTRIBUTING、FADING、UNKNOWN。强化 Breadth、leader count、turnover share、persistence、expansion、concentration。缺失不能猜成 EMERGING。

Pattern registry：pattern_id、pattern_version、family、required_features、detect、entry_rule、invalidation_rule、exit_rule。现有 Pattern 包括 high_volume_breakout、single_bull_hold、ma_compression_breakout、ma60_breakout_retest、ma5_momentum_pullback；Triple Golden Cross 可以保留，但不能天然默认优先。

同一 `strategy.evaluate(context)` 服务 HistoricalContext 和 LiveContext；所有规则变更产生新版本并重新验证证据。

## 5. Trade 是核心实体

每个完整 round trip 应保存：round_trip_id、symbol、pattern_id/version、signal_date/time、entry_date/price、regime_at_signal/confidence/data_quality、theme/lifecycle/confidence/data_quality、exit_date/price/reason、regime_at_exit、theme_lifecycle_at_exit、holding_days、gross_return/net_return、MFE/MAE。

与当前模型字段名的映射必须由开发计划明确，不为展示别名复制生产状态。BUY↔SELL 按唯一 round_trip_id 对应；signal 和 execution 分开：T 日收盘 signal 最早下一可执行交易时点，盘中 bar close signal 最早下一可执行 bar。A 股 T+1、停牌、涨跌停、滑点与成本必须真实处理。

Exit 覆盖 STOP_LOSS、TAKE_PROFIT、TRAILING_STOP、PATTERN_INVALIDATED、TREND_BREAK、TIME_EXIT、REGIME_EXIT、THEME_EXIT。每种退出是版本化策略契约；同 bar 多个条件冲突采用预先声明的保守执行规则，不挑最有利结果。

## 6. 统计与策略选择

正式 key：Regime × Lifecycle × Pattern ID × Pattern Version。

统计至少包含 Trades、Win Rate、Average/Median Return、Average Winner/Loser、Profit Factor、Expectancy、Max Consecutive Losses、Trade Sequence Drawdown、Average Holding Period、MFE、MAE。它们是描述性统计，不直接等于 ENABLED。

Phase 2A：固定版本、固定参数，默认 Train=12 months、Test=3 months、Step=3 months。冻结由 Train 拟合的阈值与决策规则；Test 只做因果计算。输出每 fold OOS 结果、Median Expectancy/PF、Worst Fold、Total OOS Trades，状态仅 INSUFFICIENT_DATA、UNSTABLE、STABLE_CANDIDATE。

Phase 2B：基于 OOS stability、sample size、context quality、版本、recency、regime/lifecycle match 形成 Pattern Enablement Table。未来 performance decay 只能版本化引入，不能自动偷偷调参。

## 7. Candidates、LLM 与实时系统

Candidate 流程：全市场 → Tradable Universe → Enabled Patterns → Matches → Liquidity/RS/Theme/Risk → Ranking。评分逐步考虑 Pattern Strength、RS、流动性、主题强度/周期、Regime fit、leader status、量能结构、趋势质量、历史 OOS 证据。真实符合条件的数量为 0/3/8 时输出对应数量，不能补齐 10。

LLM 仅负责新闻、重大事件、公告背景、异常风险、候选解释、有限 Risk Veto；不得发明 Pattern、股票、买卖点，或绕过 deterministic 风险限制。

实时状态：WATCH、BUY_READY、BUY_TRIGGERED、HOLD、SELL_WARNING、SELL_TRIGGERED、INVALIDATED。每次提醒附状态变化、symbol、Pattern/version、Context、entry/invalidation/stop、数据时间、证据与原因。BUY_TRIGGERED 是 signal/intent 状态，不自动等于成交。

Paper 链：Signal → Risk → Intent → Paper Order → Fill → Position → Exit。Risk 检查可交易、停牌、涨跌停、持仓、已有订单、现金、单股/总仓位、行业集中度、单笔/组合风险与滑点。

自动交易链：Pattern → Router → Candidate → Risk → Intent → Execution → Broker；禁止 Pattern 直连 Broker。长期默认 paper，allow_real_execution=false，实盘必须显式授权。

## 8. WebUI 与日常自动化

Dashboard：Current Regime/confidence、risk appetite、themes/lifecycle、enabled patterns、candidates、positions、alerts、daily P&L。
Candidates：symbol/theme/pattern/state/score/entry/stop/OOS evidence。
Positions：entry/current price、P&L、MFE/MAE、stop、holding days、Pattern、entry/current Regime、exit conditions。
Backtest：按 Pattern/Regime/Lifecycle/version/date 过滤，呈现 trades、win rate、expectancy、PF、drawdown、equity curve、distribution、trade list。
Trade Detail：signal snapshot、entry/exit 原因及执行、Context、盈亏、MFE/MAE。
Strategy Lab：跨 Pattern/Regime 的稳定性比较，不默认按总收益排榜。

盘前：更新数据、Context、policy、watchlist；盘中：更新行情、scan、candidates、BUY/SELL alerts；收盘：交易结果、Daily Review、research evidence。不得使用盘后修订数据反写当时决策。

## 9. 已验收基线与阶段顺序

Phase 1 Unified Trading Model、Phase 1.5 Attribution Integrity：作为既有基础，后续改动仍需回归验收。
Phase 1.5C 实际验收 PASS：producer `9d2a22cfc20c474bf17d94434b259cf08a231b61`；artifact commit `1b24d2f8b97ce170559b293e266d30ce2c3753dd`；10 symbols；27 closed/valid；0 invalid/duplicate/missing IDs；27 usable；UNVERIFIED_CACHE；20 physical adjusted/raw hashes 匹配。
本地 Python 3.11：202 passed、1 xfailed；GitHub Actions run `37113300450`，tested SHA 为 artifact commit，success。

后续顺序：2A Walk-Forward → 2B policy/router → 3 Context enhancement → 4 Candidate Engine → 5 Live Decision/Alerts → 6 Paper → 7 WebUI integration → 8 limited auto execution。当前优先级禁止继续无限扩展 Research Grade；不因上述基线 PASS 宣称全市场覆盖、所有 Regime 或实盘获利已获证实。

## 10. 原附件章节覆盖索引

1–4、60–64：目标、架构、最高原则（§1–2、§9）。
5–6：数据与等级（§3）。7–13：Context、Pattern、统一逻辑（§4）。
14–17：Trade、signal/entry、exit（§5）。18–30：矩阵、OOS、stability、policy/router（§6）。
31–34：候选、Top cap、score、LLM（§7）。35–41：实时、状态、提醒、Paper、Risk、auto 默认（§7）。
42–49：全部 WebUI 页面与日常自动化（§8）。50–59：已完成基线和阶段路线（§9）。
