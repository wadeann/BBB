---
name: a-share-signal-engine
description: 将A股交易笔记转化为可审计、可回测、可通过MCP执行、可按行情路由策略、具备完整事件日志与可重放能力，并具有受控复盘学习闭环的选股/交易信号流程。支持研究、回测、自动模拟盘和真实资金订单提案；默认不允许LLM绕过风控直接实盘下单。
version: 5.0.0
language: zh-CN
---

# A股选股与交易信号 Skill v5

## 1. 目标与职责边界
把 `references/` 中的主观交易笔记转成：

`交易日/数据源检查 → 大盘环境 → 板块环境 → 股票池 → 硬过滤 → 信号识别 → 多因子评分 → 组合风控 → 订单意图 → 模拟执行/实盘订单提案 → 复盘`

LLM是**决策编排器与解释层**，不是行情源、指标计算器、交易所规则引擎或最终风控裁决器。

支持模式：
- `research`：研究和解释，不下单；
- `backtest`：历史回测，不调用执行工具；
- `paper`：允许调用模拟交易 MCP；
- `live_proposal`：只输出/注册真实资金订单提案，不直接下真实订单。

**重要**：当前已知 `mcp_exec_place_order` 的接口说明为“模拟交易下单”，因此 v4 将它视为 `paper` 专用。除非未来新增并明确标注为真实券商下单的工具，否则不得把该接口用于真实资金。

## 2. 不可把笔记中的断言当成事实
“胜率80%+”“后期必有大涨”“第7日大概率上涨”“妖股沿MA5”“主力吸筹/洗盘/出货”等仅视为待验证假设。LLM不得把不可观测的主观意图当事实，只能映射为可观测代理变量：价格、成交量、换手率、筹码集中度、资金流、公告、财报、板块相对强弱等。

## 3. MCP 使用总原则
详细工具映射见 `references/mcp-tool-policy.md`。

### 3.1 禁止“把53个工具全调一遍”
按阶段和成本调用。批量筛选优先使用批量/筛选工具，只有进入候选池后才调用单票深度工具。

### 3.2 数据可信度与交叉验证
- 批量扫描：优先 `mcp_intel_query_batch_data`、固定模板的 `mcp_intel_wencai_search` / `mcp_intel_tdx_screener`；
- 历史K线：优先健康的数据源；可用时以 `mcp_intel_tdx_kline` 为主、`mcp_intel_fetch_kline` 为备；
- 指标：优先 `mcp_intel_get_technical_indicators`，关键指标可由程序基于K线复算；
- 最终订单价格：必须使用 `mcp_intel_query_multi_source_quote` 交叉核验，必要时再用 `mcp_intel_tdx_quotes`；
- 数据源异常：调用 `mcp_intel_market_source_health` / `mcp_intel_tdx_health`，若关键行情无法验证则禁止创建订单意图。

### 3.3 LLM不得自创工具返回值
工具缺失字段、超时、异常、冲突时，必须标记 `unknown/degraded`；不得凭经验补齐。

### 3.4 AGY限制
`mcp_intel_analyze_stock_with_antigravity` 仅在用户明确要求 AGY/Antigravity 分析时调用，不能作为默认选股链路。

## 4. 必需输入
单票最低要求仍须满足 `schemas/market_snapshot.schema.json`：
- 日线 OHLCV 至少 260 个交易日，复权口径明确；
- 当日/前日涨跌停价、停牌状态、证券板块、风险警示状态、上市天数；
- 流通股本/流通市值；
- 财务数据带可用于时点判断的披露/发布时间；
- 盘中策略需要对应分钟/盘口/集合竞价数据。

此外，所有新增开仓判断都必须有 `schemas/market_context.schema.json` 所描述的大盘环境；需要板块过滤的策略还必须有 `sector_context`。

## 5. 标准运行状态机

### 5.1 PRECHECK
调用：
1. `mcp_intel_is_trading_day()`；
2. `mcp_intel_trading_sessions()`；
3. `mcp_intel_tdx_health()`；
4. 必要时 `mcp_intel_market_source_health()`。

规则：
- 非交易日：不运行盘中/下单流程，可运行研究/回测；
- 关键数据源不可用且无可靠备源：`DEGRADED_NO_ORDER`；
- 不因工具异常自动切换到主观判断。

### 5.2 MARKET_CONTEXT
必须构建大盘环境，至少包含：
- 2–3个可配置宽基/风格指数的日K趋势；
- `mcp_intel_fetch_market_health()` 的涨跌停、炸板、连板梯队等市场健康信息；
- `mcp_intel_get_limitup_ladder()` 的情绪高度/梯队连续性；
- `mcp_intel_get_mainline_lanes()` 的主线强度；
- 可选 `mcp_intel_fetch_hot_signals()` 作为短线情绪辅助。

基准代码必须配置化，不能写死为唯一指数。默认建议覆盖“大盘/中小盘/成长”三类代表指数；若某指数数据不可用，应记录降级而不是伪造。

### 5.3 UNIVERSE_BUILD
使用固定、可审计的筛选模板生成股票池。优先：
- `mcp_intel_wencai_search`；
- `mcp_intel_tdx_screener`；
- `mcp_intel_screen_stocks`。

自然语言筛选词必须来自配置模板，禁止LLM临场把条件改宽以“凑股票”。

### 5.4 BATCH_SCREEN
对股票池调用：
- `mcp_intel_query_batch_data` 获取实时/收盘快照；
- 先执行证券状态、流动性、涨跌停、上市天数等硬过滤；
- 再执行可以批量计算的趋势/量价初筛。

建议先把数千只股票压缩到 50–200 只，再做深度分析。

### 5.5 DEEP_DIVE
只对初筛候选调用：
- `mcp_intel_tdx_kline` / `mcp_intel_fetch_kline`；
- `mcp_intel_get_technical_indicators`；
- `mcp_intel_get_chip_distribution`；
- `mcp_intel_get_fund_flow`；
- `mcp_intel_tdx_f10` / `mcp_intel_get_financial_report`；
- `mcp_intel_tdx_news` / `mcp_intel_search_news`。

新闻、筹码、资金流均为辅助证据，不得覆盖硬退出和组合风控。

### 5.6 PORTFOLIO_RISK
创建订单意图前必须读取：
- `mcp_exec_get_balance()`；
- `mcp_exec_get_positions()`；
- `mcp_exec_get_orders()`；
- `mcp_exec_get_today_trades()`；
- `mcp_exec_get_pnl()`；
- `mcp_risk_daily_pnl()`；
- `mcp_risk_get_blacklist()`。

然后使用 `mcp_risk_check_intent` 或 `mcp_risk_batch_check`。风控 MCP 的 `REJECT` 不允许 LLM 覆盖。

### 5.7 ORDER_INTENT 与 PAPER_EXECUTION
每个意图必须有唯一 `intent_id`，且固定绑定：symbol、direction、max_quantity、价格约束、过期时间、理由、策略ID。

paper 模式顺序：
1. 多源实时行情最终核验；
2. risk check；
3. `mcp_exec_register_approved_intent`；
4. `mcp_exec_place_order`；
5. 查询订单与成交回执。

`live_proposal` 模式到第2步后只产生 `ORDER_PROPOSAL`；若未来接入真实券商，还必须由独立真实执行适配器与人工确认负责。

## 6. 每日执行时间与任务（Asia/Shanghai）

### 6.1 时间源与总原则
- 所有盘中调度使用交易所当地时区 `Asia/Shanghai`，不得使用用户设备时区推断交易阶段。
- 每个交易日开始先调用 `mcp_intel_is_trading_day()` 与 `mcp_intel_trading_sessions()`；**运行时工具返回的交易时段优先于本文件静态默认值**。交易所临时调整、休市或特殊安排时不得硬套固定时钟。
- 静态默认股票竞价时段仅用于调度兜底：09:15–09:25 开盘集合竞价；09:30–11:30、13:00–14:57 连续竞价；14:57–15:00 收盘集合竞价。
- 09:20–09:25、14:57–15:00 属于不可撤单窗口，Skill 默认在其开始前进入订单冻结，避免 LLM 在不可撤销阶段临时改变主意。
- “允许分析”不等于“允许下单”。每个 phase 必须显式声明 `allowed_actions` 与 `forbidden_actions`。
- `config/phase_permissions.yaml` 必须由调度/执行程序做**确定性权限校验**；若 LLM 输出与权限表冲突，以权限表拒绝执行，不能只依赖 Prompt 自律。
- 盘中不得反复对全市场做深度分析；全市场重扫描原则上集中在收盘后，盘中只做候选池、持仓和主线增量更新。

完整时间表见 `references/daily-workflow.md`，机器可读配置见 `config/schedule.yaml`。

### 6.2 强制阶段状态机
调度器每次调用 LLM 必须传入 `phase`。合法 phase：

`RECOVERY_SYNC → PREOPEN_PRECHECK → PREOPEN_CONTEXT → OPEN_AUCTION_OBSERVE → OPEN_AUCTION_FREEZE → OPEN_RECONCILE → OPEN_STABILIZE → ENTRY_WINDOW_AM → MORNING_MONITOR → MORNING_CLOSE_PROTECT → MIDDAY_REVIEW → PM_PRECHECK → PM_STABILIZE → ENTRY_WINDOW_PM → LATE_SESSION_MANAGE → CLOSE_AUCTION_FREEZE → CLOSE_RECONCILE → EOD_UNIVERSE_SCAN → EOD_DEEP_DIVE → NIGHT_EVENT_CHECK`

LLM 不得自行跳过阶段后直接下单。若调度器在任意阶段重启，必须先进入 `RECOVERY_SYNC`。

### 6.3 08:45–09:00 `PREOPEN_PRECHECK`
目标：确认今天能不能运行自动交易。

必须：
- `mcp_intel_is_trading_day`
- `mcp_intel_trading_sessions`
- `mcp_intel_tdx_health`
- 必要时 `mcp_intel_market_source_health`
- `mcp_exec_get_balance`
- `mcp_exec_get_positions`
- `mcp_exec_get_orders`
- `mcp_exec_get_today_trades`
- `mcp_risk_daily_pnl`
- `mcp_risk_get_blacklist`

动作：
- 同步昨夜保存的候选与当前真实/模拟账户状态；
- 检查是否存在隔夜未完成订单、持仓数量变化、账户资金差异；
- 发现无法解释的不一致时进入 `DEGRADED_NO_ORDER`。

禁止：新开仓、根据盘前旧报价计算成交价。

### 6.4 09:00–09:15 `PREOPEN_CONTEXT`
目标：形成当日第一版 market/sector gate，并复核昨晚候选。

默认调用：
- 基准指数 K 线；
- `mcp_intel_fetch_market_health`；
- `mcp_intel_get_mainline_lanes`；
- `mcp_intel_get_limitup_ladder`（使用最近可用交易日数据）；
- `mcp_jin10_list_calendar`（可选，仅宏观事件风险）；
- `mcp_intel_get_watchlist`；
- 对**持仓与最终候选**调用新闻/公告核验。

输出：
- `market_regime_preopen`
- `mainline_snapshot`
- `candidate_plan_am`
- `position_exit_watch`

禁止：因一条新闻临时扩大仓位；禁止盘前用昨天收盘价格直接生成可执行订单。

### 6.5 09:15–09:19:30 `OPEN_AUCTION_OBSERVE`
目标：观察集合竞价，不追价。

调用候选与持仓的实时/盘口工具，不做全市场深挖。
- `mcp_intel_query_batch_data`：候选池；
- 必要时 `mcp_intel_tdx_quotes`：盘口；
- 可更新市场健康/热点，但不得据一次瞬时异动重写策略。

默认动作：`WATCH / CANCEL_EXISTING_IF_ALLOWED / REDUCE_RISK_PROPOSAL`。
默认禁止：`NEW_ENTRY`。

若以后确实要做集合竞价策略，必须作为独立策略 ID 回测，不得复用日线策略逻辑。

### 6.6 09:19:30–09:25 `OPEN_AUCTION_FREEZE`
这是 Skill 的保守冻结窗，覆盖交易所 09:20 起不可撤单阶段。

- 不创建新的策略性买单；
- 不修改已进入不可撤单阶段的订单；
- 只记录虚拟参考价/匹配量等可用信息；
- 若发现严重风险，仅生成 `URGENT_REVIEW`，不得假设能撤销已不可撤订单。

### 6.7 09:25–09:30 `OPEN_RECONCILE`
目标：处理开盘集合竞价结果。交易所此段通常不接受竞价申报，因此默认 `NO_NEW_ORDER`。

必须：
- 查询订单与成交；
- 更新账户现金/持仓；
- 对昨晚候选重新计算 opening gap、可成交性和风险收益比；
- 高开/低开超出配置阈值的候选降级，不允许“为了买到”追价。

### 6.8 09:30–09:35 `OPEN_STABILIZE`
默认只允许：
- 已持仓强退出；
- 风险降低；
- 撤销仍可撤的异常挂单；
- 更新 market/sector 状态。

默认禁止新开仓。`open_stabilization_minutes` 可配置，但 LLM 无权临场改成 0。

### 6.9 09:35–10:30 `ENTRY_WINDOW_AM`
上午主要新开仓窗口。

仅处理 `candidate_plan_am` 和明确的盘中新信号，不再深挖数千只股票。

每个新仓必须依次：
1. 最新 market gate；
2. 最新 sector gate；
3. 个股信号仍有效；
4. 账户/持仓/挂单去重；
5. 多源实时报价；
6. `mcp_risk_check_intent` / batch check；
7. 注册 intent；
8. paper 模式才允许 place_order；
9. 查询订单回执。

买入后不得因短时盈利马上提高下一笔风险预算。

### 6.10 10:30–11:20 `MORNING_MONITOR`
目标：持仓管理 + 候选增量更新。

- 持仓优先于新机会；
- 可使用 `fetch_hot_signals`、主线变化、市场健康做增量更新；
- 不进行第二次全市场逐股深挖；
- 新开仓数量受 `max_new_positions_per_day` 和剩余风险预算约束；
- 同一 symbol 当日重复入场默认禁止。

### 6.11 11:20–11:30 `MORNING_CLOSE_PROTECT`
默认停止普通新开仓，处理：
- 未成交订单；
- 风险仓位；
- 上午策略失效；
- 午间前账户对账。

若策略必须在此窗口开仓，需独立配置 `allow_morning_close_entry=true`，不能由 LLM临时打开。

### 6.12 11:30–12:45 `MIDDAY_REVIEW`
无交易执行。

必须完成：
- 上午订单/成交/滑点复核；
- 当前 market/sector 状态刷新；
- 持仓退出优先级；
- 下午候选重新排序；
- 风险预算剩余量计算。

不得因为上午亏损而“加大下午仓位追回损失”。

### 6.13 12:45–13:00 `PM_PRECHECK`
重新检查行情源、账户状态、黑名单、重大午间新闻/公告，并生成 `candidate_plan_pm`。

禁止在 13:00 前假设下午已经成交。

### 6.14 13:00–13:05 `PM_STABILIZE`
与开盘稳定窗相同：默认只处理退出、撤单和风险降低，不开新仓。

### 6.15 13:05–14:30 `ENTRY_WINDOW_PM`
下午正常开仓窗口。规则同 `ENTRY_WINDOW_AM`。

对于需要日线收盘确认的形态：此时只能标记 `PROVISIONAL`，不得把尚未发生的 15:00 收盘价当作已确认数据。

### 6.16 14:30–14:56:30 `LATE_SESSION_MANAGE`
目标：减少临近收盘的不确定订单，而不是追求“必须买满”。

默认：
- 14:30 后提高新仓证据门槛；
- 需要“收盘确认”的日线策略仍不得伪造最终收盘信号；
- 14:50 后原则上只处理已有候选、退出和撤单，不新增盘中临时发现的陌生标的；
- 14:56:30 进入本 Skill 的收盘竞价前订单冻结。

尾盘策略如需例外，必须单独回测、单独 strategy_id、单独配置。

### 6.17 14:56:30–15:00 `CLOSE_AUCTION_FREEZE`
覆盖交易所 14:57–15:00 不可撤单窗口：
- 禁止普通新开仓；
- 禁止假设能够撤销不可撤订单；
- 只做状态记录和必要告警；
- 收盘集合竞价完成前，不把虚拟价格当最终收盘价。

### 6.18 15:00–15:15 `CLOSE_RECONCILE`
目标：先对账，不立刻“看图讲故事”。

调用：
- `mcp_exec_get_orders`
- `mcp_exec_get_today_trades`
- `mcp_exec_get_balance`
- `mcp_exec_get_positions`
- `mcp_exec_get_pnl`
- `mcp_risk_daily_pnl`

记录实际成交、未成交、滑点、撤单结果和风险拒绝原因。

### 6.19 15:15–16:15 `EOD_UNIVERSE_SCAN`
这是默认的**全市场主扫描窗口**。

流程：
`UNIVERSE_BUILD → BATCH_SCREEN → MARKET/SECTOR FILTER → shortlist`

- 使用正式收盘后的日线数据；
- 数千股先批量筛选，压缩到约 50–200；
- 禁止对全市场逐票调用新闻/F10/筹码/多源报价；
- 此阶段不下单。

### 6.20 16:15–17:00 `EOD_DEEP_DIVE`
对 shortlist 深挖：K线、技术、筹码、资金、财务、板块、公告/新闻。

输出下一交易日候选：
- `A`: 满足核心条件，待次日盘前复核；
- `B`: 接近触发，仅观察；
- `REJECT`: 明确失效。

可通过 `mcp_intel_update_watchlist` 写入候选及原因，但 watchlist 不等于买入授权。

### 6.21 20:30–21:00 `NIGHT_EVENT_CHECK`
这是可选低频事件检查，只处理：
- 当前持仓；
- A 级候选；
- 明确影响次日交易的公司公告/重大宏观事件。

允许更新 `next_day_block_reason` / `event_risk`，不得夜间下单，也不得因一条非结构化消息直接把 B 级候选升级为买入。

### 6.22 非交易日
- 禁止调用执行链路；
- 可运行研究、回测、参数验证、周复盘；
- 不得把非交易日行情缺失误判为数据源故障。

### 6.23 盘中轮询与高频风险
LLM 不是高频风控进程。
- 价格止损、账户风险、订单状态等高频安全逻辑应尽可能由确定性程序/Risk Engine 执行；
- LLM 盘中轮询只做策略级判断，默认 5–15 分钟级，不应承担秒级止损；
- 如果系统没有独立的高频风险守护，则必须在部署文档中显式标记该能力缺口，不能声称具备秒级自动止损。

### 6.24 幂等、防重复与冷却
- 每个订单意图生成稳定 `intent_id`，至少包含：交易日、strategy_id、symbol、direction、phase、signal_version；
- 创建新 intent 前必须查询当日订单/成交，避免调度重跑造成重复单；
- 默认 `one_entry_per_symbol_per_day=true`；
- 止损后同日重新买回默认关闭；
- 同一信号在没有新 bar / 新事件 / 新风险状态时不得重复触发。

### 6.25 `RECOVERY_SYNC`：任何重启都先恢复状态
系统在盘中或盘前启动/重启时，第一阶段必须是 `RECOVERY_SYNC`：
1. 获取交易日与交易阶段；
2. 查询余额、持仓、挂单、当日成交、PnL；
3. 恢复已注册 intent 与本地状态；
4. 核对本地记录与 MCP 实际状态；
5. 完成一致性检查后才能进入当前时段对应 phase。

无法解释的差异 → `DEGRADED_NO_ORDER`，禁止“猜测账户状态后继续交易”。

## 7. 大盘行情过滤：必须加入，但分“门控”和“评分”两层

### 6.1 基准趋势
对每个配置基准计算：
`MA20_slope=(MA20_t-MA20_t-5)/MA20_t-5`

单指数状态：
- `up`: close > MA20 且 MA20_slope > 0；
- `down`: close < MA20 且 MA20_slope < 0；
- `mixed`: 其他。

多指数汇总：
- `trend_risk_on`：多数基准为 `up`；
- `trend_risk_off`：多数基准为 `down`；
- 其余为 `trend_neutral`。

### 6.2 市场健康与短线情绪
使用 `mcp_intel_fetch_market_health`、`mcp_intel_get_limitup_ladder`。以下阈值仅是初始配置，必须在回测中验证，LLM不能临场修改：
- 炸板率高于 `weak_blowup_rate`；
- 跌停家数相对涨停家数显著恶化；
- 连板高度/晋级梯队明显收缩；
- 以上任两项同时转弱，可把短线情绪标记为 `weak`。

默认 `weak_blowup_rate=0.45`，属于实验参数，不是市场真理。

### 6.3 最终 market_regime
- `risk_on`：指数趋势多数向上，且市场健康不为 weak；
- `risk_off`：指数趋势多数向下，且市场健康为 weak；
- `neutral`：其他情况。

### 6.4 市场门控
- `ma5_momentum_pullback`、连板/龙回头、强势突破等高β短线策略：`risk_off` 禁止新增仓；
- 普通波段策略：`risk_off` 可保留 WATCH，但新开仓风险预算乘 `risk_off_position_multiplier`，默认 0.5；
- 已持仓退出信号不受 risk_off 限制，退出优先执行；
- 市场环境不能把一个硬风控失败的股票“加分救回来”。

## 8. 板块行情过滤：建议加入，且A股短线应高于个股新闻的优先级

### 7.1 股票 → 板块映射
优先从 `mcp_intel_tdx_f10(symbol, module="basic")` 获取行业/板块信息；若不足，可用固定问句调用 `mcp_intel_wencai_search` 查询所属行业/概念。映射不确定时必须标记 `sector_mapping_quality=low`。

### 7.2 板块趋势
对主要行业板块调用 `mcp_intel_fetch_sector_history(code)`，计算：
- close 与 MA20/MA60 关系；
- MA20 斜率；
- 20日涨幅；
- 相对所选宽基的20日超额收益。

得到 `sector_trend_score` 0–100。

### 7.3 主线强度
调用 `mcp_intel_get_mainline_lanes(top_n=5)`。若股票所属板块进入主线榜，使用工具返回的攻击分、梯队分、空间地位、资金百分位等形成 `mainline_score`；不要让LLM根据板块名字自行判断“热门”。

### 7.4 sector_strength_score
默认：
`sector_strength_score = 0.6 * sector_trend_score + 0.4 * mainline_score`

若无主线数据，则只使用可验证的板块趋势并降低数据质量等级；不得自动把缺失值当 0。

分层：
- `strong`: >= 70；
- `neutral`: 45–69；
- `weak`: < 45。

阈值必须可配置并经样本外回测。

### 7.5 板块门控
- 高β短线策略：板块 `weak` 默认不得新增仓；
- 普通波段：板块 `weak` 扣分并降低仓位，不做绝对一票否决；
- 若个股存在极强独立事件驱动，可进入 WATCH，但需要新闻/公告证据且不能绕过风险检查。

## 9. 硬过滤（任何评分之前）
默认剔除：停牌、退市整理、数据异常、上市不足20个交易日、无法合法下单或无法可靠估算成交的证券。ST/风险警示股默认剔除，可配置但必须单独回测。北交所默认关闭，除非执行层已加载对应现行规则。

禁止把“挂涨停价=一定成交”写入策略。涨停/跌停附近必须使用 `fill_probability=unknown` 或基于盘口的成交模型；回测不得假设触价即成交。

## 10. 可计算信号
### 9.1 三线金叉 `triple_golden_cross`
最近5个交易日窗口内同时满足：MA5上穿MA10、VOL_MA5上穿VOL_MA10、MACD.DIF上穿DEA、近20日反弹幅度不超过参数 `max_rebound_pct`（默认25%）、上涨日量能较下跌日改善。

### 9.2 出水芙蓉 `ma_convergence_breakout`
MA5/10/20收敛度 <=2.5%；当日实体涨幅>=5%；close同时上穿MA5/10/20；volume>=1.5*VOL_MA20。

### 9.3 灵猴探路 `ma60_breakout_retest`
近15日有效突破MA60；突破日volume>=1.5*VOL_MA20；之后1–10日回踩MA60、距离<=2%；回踩量<=突破日70%；当前close>MA60并出现右侧确认。

### 9.4 单阳不破 `single_bull_hold`
基准阳线单日涨幅>=5%或连续2–3阳累计>=7%；之后最多8根K线low不低于基准阳线low；当前向上突破且volume>VOL_MA5；市场不为risk_off。

### 9.5 长阳七星 `long_bull_day7`
仅作实验时间因子：标志阳线>=5%且volume>=1.5*VOL_MA20；后续5–7日振幅收敛、未破标志阳线low；第6–8日出现突破才触发；不允许仅因“第7天”买入。

### 9.6 巨量突破 `high_volume_breakout`
volume>=2.0*VOL_MA20；记录巨量日high；后续1–10日close>该high才确认；突破后3日内无法维持且出现长上影/放量滞涨则失效。

### 9.7 地量支撑小阳 `low_volume_support_bull`
volume<=0.5*VOL_MA20且处近20日低分位；距明确支撑<=2%；当日阳线实体0.3%–3%；仅作确认因子。

### 9.8 MA5强势回踩 `ma5_momentum_pullback`
连续3日close>MA5且逐日创新20日收盘新高；首根回调阴线出现；次日价格回到MA5±2%；仅在非一字板、可成交、market_regime != risk_off 且 sector_strength != weak 时生成候选。

### 9.9 顶部/退出信号
至少实现 `shooting_star_high`、`volume_price_divergence`、`ma20_break`、`trendline_break`、`ma_bearish_cut`。任何强退出信号覆盖加仓信号。

## 11. 基本面因子
只用 as_of 时点已经公开的数据：`net_profit_yoy`、`roe_yoy_delta`、`gross_margin_yoy_delta`、`core_business_ratio`、`earnings_quality`。旧笔记的固定阈值不得视为跨行业真理，应参数化并按行业分位数回测。

## 12. 评分
仍输出0–100的**信号强度**，不是上涨概率。为避免仅因加入板块过滤而改变整套历史评分，v3暂保持原总权重：
- 趋势 25；
- 量价 25；
- 形态 20；
- 基本面 15；
- 市场/板块 10，其中默认 `market=4`、`sector=6`；
- 可成交性 5。

`score >= 75` 才可进入候选池；市场/板块门控与硬风控独立于分数，门控失败时即使 score>=75 也不能生成 ENTRY_CANDIDATE。

## 13. 筹码、资金流、新闻的使用方式
- `chip_distribution`：用于判断成本区、套牢盘、集中度，只能作辅助；
- `fund_flow`：不得把“大单净流入”直接解释为“主力必然买入”；
- `news`：只对持仓与最终候选做利空/事件核验，不对全市场逐票搜索；
- 同一事实如可由公告/F10直接验证，优先结构化来源；
- 新闻情绪不得覆盖交易规则、退出信号和风控拒绝。

## 14. 国际/宏观 Jin10 工具
`jin10` 不进入每只股票的默认评分，只作为市场风险背景：
- 盘前可调用 `mcp_jin10_list_calendar()` 检查重大宏观事件；
- 只有需要解释突发宏观冲击时才搜索快讯/新闻；
- 黄金、外汇报价仅在对应策略或宏观风险判断确有需要时调用；
- 不因一条国际快讯自动清仓或满仓。

## 15. 风控与仓位
默认参数必须可配置，并由回测优化而不是LLM临场修改：
- 单笔计划风险 <=0.5%账户权益；
- 单票初始仓位<=10%；
- 总股票仓位<=50%；
- 同行业总仓位<=20%；
- risk_off 下普通波段新仓风险预算默认乘0.5，高β短线禁止新仓；
- 止损使用结构止损与ATR约束；
- 不对亏损仓位无条件补仓；
- 达到组合日亏损/最大回撤阈值后停止新增仓位。

仓位公式：`shares=floor((equity*risk_per_trade)/abs(entry-stop)/lot_size)*lot_size`，再受现金、单票、行业、总仓位和风控MCP限制。

## 16. 信号冲突优先级
`交易规则/数据完整性 > risk MCP > 组合风控 > 强退出 > 市场门控 > 板块门控 > 入场信号 > 加仓信号 > 新闻/资金流辅助`

买入与强退出同时出现：不得新开仓。

## 17. 输出
单票严格符合 `schemas/signal_output.schema.json`。新增推荐字段：
- `market_context`；
- `sector_context`；
- `tool_trace`（只记录工具名、时间、状态，不记录敏感凭证）；
- `risk_result`；
- `intent_id`（若生成订单意图）。

必须包含 `reasons_against` 至少1条反证。不得输出“必涨/稳赢/翻倍/主力一定会”等确定性措辞。

## 18. 回测与验证门槛
上线前完成 walk-forward / out-of-sample，覆盖牛熊震荡；纳入手续费、税费、滑点、涨跌停无法成交、停牌、退市、复权、财报披露时点；防止未来函数、幸存者偏差。

除常规 CAGR、最大回撤、Sharpe、Sortino、Calmar、胜率、盈亏比外，必须新增分层分析：
- `market_regime` 分层；
- `sector_strength` 分层；
- 主线/非主线分层；
- 各策略在 risk_on / neutral / risk_off 中的独立表现。

只有当加入大盘/板块过滤后在样本外改善“回撤/收益质量”而非仅提高历史胜率时，才保留对应门控参数。

## 19. 复盘
paper 模式成交后，通过 `mcp_exec_get_orders`、`mcp_exec_get_today_trades`、`mcp_exec_get_pnl` 记录：
- 信号与实际成交偏差；
- 滑点；
- 未成交原因；
- risk reject 原因；
- 市场/板块状态；
- 退出是否遵循规则。

LLM可以总结，但不得根据单笔盈亏修改策略参数。

## 20. 参考
- 每日调度：`references/daily-workflow.md`
- 调度器调用模板：`references/scheduler-prompts.md`
- MCP策略：`references/mcp-tool-policy.md`
- 默认参数：`config/defaults.yaml`
- 时间配置：`config/schedule.yaml`
- 阶段执行权限：`config/phase_permissions.yaml`
- 原始交易笔记：`references/*.md`

如原始笔记与本文件冲突，以本文件的可验证定义、数据时点、MCP调用策略和风控为准。


## 19. 收益目标：不得把“月收益30%”写成强制交易 KPI

系统可以记录收益目标，但不得以“必须完成某月收益率”为理由放宽入场条件、提高仓位、增加杠杆、追涨或覆盖风控。

- 月收益30%可以作为**研究目标/压力测试情景**，不能作为生产系统承诺。
- 生产系统首要目标是：风险受控、规则一致、可审计、长期正期望。
- 评价策略必须同时看：收益、最大回撤、收益回撤比、胜率、盈亏比、期望值、换手、滑点、容量、不同市场状态下的稳定性。
- 当月收益落后目标时，LLM不得采取“补收益”行为。
- 当月收益超出目标时，也不得因此自动放松风控。

`config/defaults.yaml` 中的 `performance_objective` 仅用于报告与研究，不进入订单放行逻辑。

## 20. Regime Strategy Router：按大盘/板块状态选择策略

在任何新开仓信号评分前，必须先构建 `strategy_route`，详见：
- `references/regime-strategy-routing.md`
- `config/strategy_router.yaml`
- `schemas/strategy_route.schema.json`

路由输入至少包含：
1. `market_regime`：risk_on / neutral / risk_off；
2. `market_trend`：up / range / down / unknown；
3. `sentiment_phase`：warming / hot / divergent / cooling / panic / rebound / unknown；
4. `sector_strength`：strong / neutral / weak / unknown；
5. `sector_lifecycle`：emerging / accelerating / crowded / cooling / unknown。

路由输出必须包含：
- `allowed_strategy_families`；
- `blocked_strategy_families`；
- `position_multiplier`；
- `candidate_threshold_delta`；
- `max_new_positions_override`；
- `route_reasons`。

LLM不得“看到某只股票很好”就绕过路由；若策略族被路由阻断，则该策略不得产生新开仓。

### 20.1 默认策略族
- `trend_breakout`：三线金叉、均线收敛突破、巨量突破；
- `trend_pullback`：MA60突破回踩、MA5强势回踩、单阳不破、地量小阳；
- `rebound_reversal`：龙回头/反包类，仅在独立反转确认条件成立时使用；
- `pattern_confirmation`：长阳七星等只作为确认/加减分，默认不单独开仓；
- `exit_defensive`：风险退出、减仓、现金优先。

路由参数是实验默认值，必须经过历史回测和 walk-forward 验证后才能提高生产权限。

## 21. LLM 上下游交接协议（Handoff Contract）

任何 phase 之间、LLM 与 Risk/Exec 之间都不得仅靠自然语言交接。必须使用结构化 envelope，详见：
- `references/handoff-contracts.md`
- `schemas/handoff_envelope.schema.json`

每条消息至少包含：
- `schema_version`
- `message_type`
- `run_id`
- `trace_id`
- `message_id`
- `parent_message_id`
- `idempotency_key`
- `phase`
- `as_of`
- `data_cutoff`
- `freshness_seconds`
- `producer`
- `payload`
- `data_quality`
- `source_refs`
- `errors`
- `next_action`

### 21.1 硬性限制
下游遇到以下任一情况必须拒绝自动执行：
- schema_version 不支持；
- 必填字段缺失；
- phase 不匹配；
- 数据超过 freshness；
- symbol / direction / quantity / intent_id 与上游不一致；
- 上游状态为 degraded/reject；
- 风控结果不是 PASS；
- idempotency_key 已消费；
- 订单请求无法追溯到唯一 signal + risk result。

LLM可以产生“派生结论”，但不得改写原始行情、账户、风控或成交回执。

## 22. 每日复盘与受控持续改进闭环

新增 `DAILY_REVIEW` phase。完整机制见：
- `references/continuous-improvement-loop.md`
- `schemas/review_report.schema.json`
- `schemas/change_proposal.schema.json`
- `config/improvement.yaml`

### 22.1 每日闭环
`信号 → 风控 → 执行 → 成交 → 持仓结果 → 归因 → 复盘 → 改进假设`

每日复盘必须回答：
1. 今天哪些信号正确/错误？
2. 错误来自选股、行情路由、时机、仓位、执行、数据还是规则？
3. 哪些交易被正确阻止？
4. 哪些机会因为数据/风控/执行问题被错过？
5. 每个策略族在对应 market/sector regime 下表现如何？
6. 预测的 entry/stop 与实际 MFE/MAE、滑点、成交质量如何？
7. 是否发现数据漂移、参数漂移或模型输出漂移？

### 22.2 禁止日内自我修改生产策略
`DAILY_REVIEW` 只能生成 `CHANGE_PROPOSAL`，不能修改：
- score 权重；
- candidate threshold；
- risk_per_trade；
- 仓位上限；
- strategy router；
- 止损逻辑；
- phase permission；
- 生产 Prompt。

任何变更必须进入：
`proposal → historical backtest → walk-forward/OOS → paper shadow → approval → version promotion → monitoring/rollback`。

### 22.3 必须版本化
每次信号与订单至少记录：
- `strategy_version`
- `config_version/config_hash`
- `schema_version`
- `prompt_version/prompt_hash`
- `model_id`
- `code_version`
- `data_source_version`（如可用）

否则无法判断收益变化究竟来自策略、模型、数据还是执行系统。

## 23. DAILY_REVIEW 时间与权限
默认 `21:00–21:30 Asia/Shanghai` 运行：
- 只读今日信号、订单、成交、PnL、市场/板块状态和异常日志；
- 产出 `review_report` 与零到多个 `change_proposal`；
- 不下单、不撤单、不改变当天或次日生产参数。

若当日无交易，也必须复盘：候选池质量、被 gate 阻断的机会、未成交原因、数据源异常与系统健康。


## 24. 完整审计日志与每日可回放机制

生产运行必须启用审计日志，详细规范见：
- `references/audit-logging.md`
- `config/logging.yaml`
- `schemas/audit_event.schema.json`
- `schemas/daily_manifest.schema.json`

### 24.1 日志必须覆盖整个决策漏斗
至少记录：
`RUN/PHASE → MARKET_CONTEXT → SECTOR_CONTEXT → STRATEGY_ROUTE → UNIVERSE_SCREEN → DEEP_DIVE → SIGNAL_DECISION → RISK → INTENT → ORDER → FILL/CANCEL → POSITION/PNL → DAILY_REVIEW → CHANGE_PROPOSAL`。

不能只记录最终买卖结果。对全市场初筛至少保存紧凑的 `UNIVERSE_SCREEN_RECORD`，包含是否通过硬过滤、淘汰原因、预评分、market/sector gate 与数据快照哈希；进入深度分析的股票必须保存完整决策证据。

### 24.2 Append-only 与防篡改
- 生产事件日志只允许追加，不允许覆盖历史记录；
- 每条事件写入 `previous_event_hash` 与 `event_hash`，形成 SHA-256 hash chain；
- 错误修正使用新事件并引用旧 event/message，不直接改旧记录；
- SQLite/数据库只作为索引，`events.jsonl`/等价 append-only event store 为审计真相源。

### 24.3 必须可回答的回溯问题
系统必须能查询：
1. 某天为什么买入/卖出某股；
2. 某股为什么入选、为什么被淘汰；
3. 当时大盘/板块/策略路由是什么；
4. 当时用了哪个策略、配置、Prompt、模型、代码版本；
5. Risk 为什么 PASS/REJECT；
6. 计划价、实际成交价、滑点与未成交原因；
7. 当晚复盘的归因和改进假设；
8. 某次异常是否来自数据、模型、风控、执行或调度。

### 24.4 每日 Manifest
每个交易日必须生成 `manifest.json`，汇总：
- 股票池数量与候选漏斗；
- entry/watch/reject 数量；
- risk PASS/REJECT；
- order/fill/cancel；
- PnL；
- review_id；
- event_count；
- 首尾事件 hash；
- 当日 strategy/config/prompt/model/code 版本集合；
- 数据质量与系统异常。

### 24.5 两种 Replay
- `EXACT_REPLAY`：只使用当时保存的数据快照和当时生产输出，重建“当时为什么这么做”；不得访问未来数据，不得下单。
- `COUNTERFACTUAL_REPLAY`：使用当时快照，但允许换新策略/配置/模型，研究“如果当时采用新版本会怎样”；必须标记 counterfactual，永远不可调用交易执行。

### 24.6 DAILY_REVIEW 必须引用证据
复盘中的主要结论必须引用 `event_id / trace_id / intent_id / snapshot_hash` 中至少一种。LLM不得只根据自然语言摘要编造交易故事。

### 24.7 安全与脱敏
不得记录 API key、Token、密码、Cookie、券商密钥。账户标识应哈希化，异常堆栈和MCP请求/响应在入日志前必须脱敏。

### 24.8 运行时建议
初版 Runtime 可使用“append-only JSONL + SQLite 索引 + gzip snapshot”实现，不需要额外 MCP。后续可迁移到 PostgreSQL/ClickHouse/对象存储，但必须保持 event schema、hash chain 和 replay 语义兼容。

---

## Historical Backtest / Walk-Forward

历史回测必须遵循 `references/backtesting.md`。回测运行时禁止调用真实/模拟下单接口；日线收盘信号最早下一交易日成交；T+1、涨跌停、交易成本和数据可用时间必须纳入。回测结果只能作为研究证据，任何参数变更仍须经过 Walk-Forward / OOS / Paper Shadow / 审批流程。
