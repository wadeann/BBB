# BBB Trading Decision System Implementation Plan

> **For agentic workers:** 按阶段执行本计划；使用 subagent-driven-development 或 executing-plans。开发由外部开发者负责，本助手负责独立验收。本轮不执行下列开发任务。每阶段完成后提交验收，不自动进入下一阶段。

**Goal:** 从可审计历史 Trade，发展为按市场环境选择经 OOS 验证 Pattern 的实时决策、Paper 与有限自动执行系统。

**Architecture:** 复用现有 provider、BacktestEngine、scanner、router、risk/intent/audit、worker、通知与 WebUI。先建立固定版本 OOS 证据，再做证据约束的政策；后续 Context 变更必须重新取得 OOS 授权。历史与实时共享求值，所有交易经风控/intent，不为方便绕过现有边界。

**Tech Stack:** 现有 Python >=3.11、dataclass/model、YAML 配置、JSON/CSV artifact、SQLite、pytest、FastAPI 和现有浏览器 JS/CSS；沿用 MCP transport，未来 broker 接口必须另行确定。

配套：`BBB_PRODUCT_GOALS.md` 记录用户完整目标；`BBB_ACCEPTANCE_STANDARD.md` 定义 A01–H07 验收用例及全局门槛。文档中建议新增的文件/CLI 尚未实现，不是可立即运行的现有命令。

---

## 0. 决策与职责

推荐：阶段门控、先研究证据后生产授权。备选“先做实时全链再补OOS”会过早把未经验证策略用于决策；“继续先完成全部Research Grade”会偏离用户当前优先级，均不采用。

外部开发者实现并提供证据；验收者独立复跑。每阶段固定 source/config/data，先写 observable contract tests、确认失败、最小实现、针对路径验证、完整回归、真实 smoke、提交、CI与验收。不能用source字符串测试代替行为。

没有单独授权时不调参数、不部署真实下单、不向真实账户发送订单。本计划是开发规格，不是实盘授权。

基线：source `9d2a22cfc20c474bf17d94434b259cf08a231b61`；fresh smoke commit `1b24d2f8b97ce170559b293e266d30ce2c3753dd`，202 passed/1 xfailed；Phase1.5C PASS。后续外部变化先读diff，不能默认覆盖。

## 1. 仓库现实与集成位置

以下已有接口来自源码检查，不代表全部能力已运行验证：

| 领域 | 现有路径/接口 | 必须解决的缺口 |
|---|---|---|
| 回测 | `a_share_agent/backtest/{models,data,engine,portfolio,costs,metrics,report}.py`；BacktestSettings、HistoricalDataProvider、BacktestEngine.run | provider按symbol缓存、固定count未保证完整窗；折末END_OF_BACKTEST清算不适合作为普通策略退出证据 |
| Walk-Forward | `backtest/walk_forward.py` WalkForwardEngine；`backtest/service.py`、`cli.py` | 现有score grid按Calmar挑threshold，与本次“固定参数不调优”冲突；相邻inclusive测试边界重叠；fold report未独立完整持久化 |
| Research | `backtest/research.py`导出legacy lab、`research_legacy.py` | full-market正式research hard gate强于本次Trading Grade；不能删除旧gate或把弱结果伪称formal ready |
| Context | `backtest/regime.py`与`market/context.py` MarketContextBuilder | historical expanded与live legacy分开计算，尚未真实adapter parity |
| Patterns/Router | `strategy/signal_engine.py` DeterministicSignalEngine.scan、`strategy/router.py` StrategyRouter、`config/strategy_router.yaml` | scanner共享但无独立完整registry；router仅legacy/family，未消费OOS policy |
| Live | `core/orchestrator.py`、`runtime.py`、`worker.py`、`worker_state.py` | intraday目前主要monitor，不等于完整自动decision链 |
| Risk/Execution | `risk/engine.py` LocalRiskEngine、`execution/{engine,intent_store}.py` | sector参数未落实本地行业上限；paper/backtest risk不能假定等价；当前无real broker适配 |
| Notify/Web | `notifications/{dispatcher,store,formatter,channels}.py`；`market/{dashboard,workbench}.py`；`web/app.py`、`web/static/{app.js,index.html,app.css}` | 需接真实decision/OOS/position语义，复用现有audit/SSE，不另做平行消息系统 |

先检查现有符号references与调用者再修改 exported API。新增模块只为明确职责；不要重写已有engine、另建第二套scanner或router。

## 2. 共用数据契约

- ContextSnapshot：as_of、available_at、timezone、regime/lifecycle、confidence、quality(state/reasons/coverage)、feature_version、context_version、input_manifest_id。按现有模型扩展，保留正式字段映射，不制造长期别名。
- PatternSpec：id/version/family、required_features、detect/entry/invalidation/exit；现有scan规则作为实现，不因registry抽取改变逻辑。
- Trade：沿用既有Trade字段，明确gross/net单位、MFE/MAE、holding trading days；snapshot只读；新增字段必须迁移所有生产caller与序列化测试。
- Evidence key：`(regime_at_signal, theme_lifecycle, pattern_id, pattern_version)`；证据另绑定context版本、entry/exit/risk配置hash、数据manifest与生成时间，防止同Pattern版本下换exit仍借用旧证据。
- 所有artifact含schema_version、producer_git_commit、配置hash、输入manifest、coverage、methodology、状态与失败原因。现有smoke沿用`git_commit_sha`，不为了统一命名手改旧schema。
- 异常分层：RUN_FAILED/DATA_BLOCKED属于运行状态，NO_TRADE是合法决策，INSUFFICIENT_DATA是统计状态。禁止相互替代。

## 3. Phase 2A：固定规则 Walk-Forward（当前唯一允许开始的开发阶段）

### 范围与禁止项

只建立研究编排、数据窗核验、固定规则fold执行、统计与证据。不修改Pattern参数/signal逻辑、生产router、position sizing、stop loss、holding days、LLM prompts、auto execution。LLM保持关闭的固定baseline，不加入额外alpha筛选。

现有WalkForwardEngine score-grid调优不是本次2A；必须干净替换其语义并迁移CLI/service调用，不留默认仍调优的旧入口。普通backtest行为保持；折执行扩展须由显式evaluation contract触发，不能改普通交易规则。

### Task 2A.1 冻结合同与配置

Files：modify `a_share_agent/backtest/walk_forward.py`、`models.py`（仅fold/report合同）；create `config/walk_forward.yaml`、`tests/test_walk_forward_contract.py`。

- [ ] 写12/3/3、跨年/闰月、相邻边界、窗口不足测试，再实现半开月区间，转为真实交易日时保持无重复/漏日。
- [ ] 配置记录start/end、train_months=12、test_months=3、step_months=3、warmup、universe、immutable settings。step<test拒绝，部分尾折不默默当完整折。
- [ ] 记录规则/版本hash；禁止score-grid/trial selection；训练仅描述/登记证据，不改变固定参数。
- [ ] 移除/迁移旧walk-forward调优入口与文档；旧调用者不允许悄悄继承新旧混杂语义。

### Task 2A.2 Trading Grade完整窗preflight

Files：reuse `backtest/data.py` provider；create `backtest/walk_forward_preflight.py`、`tests/test_walk_forward_preflight.py`；必要时extend `data.py`获取指定interval，不改raw fallback禁令。

- [ ] 逐fold验证benchmark日历、symbol生命周期/tradability、warmup、adjusted/raw物理非空与覆盖；count=900不等于完整，必须按dates证明。
- [ ] 记录请求universe、实际覆盖/缺失、IPO不足warmup及处理，不以有非空bars就算有效。
- [ ] 临界missing adjusted/raw/hash、benchmark缺日、非连续/异常价格、未来来源、股票尚未上市fixture必须blocked。
- [ ] 以本地Trading Grade路径运行，Research Grade gate保留独立；不得移除full-market正式research gate。加入单独明确Trading Grade入口，仅覆盖本次必要硬门槛，输出grade与限制。

### Task 2A.3 独立fold执行与边界

Files：modify `walk_forward.py`、必要的`engine.py/models.py`显式研究窗口接口；tests `test_walk_forward_execution.py`、extend `test_backtest.py`。

- [ ] 每fold初始化独立portfolio/pending；train信号/仓位不进入test；test内signal→next executable entry，拒绝末日未来新买fill。
- [ ] `entry_window`限定test，`observation_window`可延长用于自然exit，不新增交易；冻结holding/stop等原值。
- [ ] 观察尾部默认最多覆盖冻结max_holding_days所需交易日及实际执行延迟；停牌等使仍未退出的仓位censored，不能人工按折末close填成交。END_OF_BACKTEST若用于账面演示必须单列settlement，不参与自然exit统计。
- [ ] 同时存在下一fold的新仓与前fold尾部管理只是研究归属，不是可直接合并的连续组合；trade归signal fold。聚合仅可合并trade统计，独立fold equity不得串成伪真实组合净值。
- [ ] 测试现有普通backtest不变，新增边界T+1/停牌/涨跌停/raw缺失、pending buy/sell/partial观察/censored路径。

### Task 2A.4 四维OOS矩阵与stability

Files：create `backtest/oos_stability.py`、`tests/test_oos_stability.py`；reuse `metrics.py`，extend仅缺失真实统计合同。

- [ ] 每fold完整trades、closed/open/censored、audit、context质量、coverage、拒绝原因，按四维key输出描述统计。
- [ ] PF=total positive net return/abs(total negative net return)，无负收益分母为0用null+NO_LOSSES，不输出inf；expectancy定义为每closed trade net_return算术均值（fraction，不是百分数）。
- [ ] fold trade sequence按exit时间/round_trip_id排序；drawdown为prod(1+net_return)相对running peak的最差下降，明确这是trade序列诊断，不是portfolio DD。MFE/MAE沿用模型约定并标单位。
- [ ] aggregate：count-based pooled描述统计与equal-fold medians分别保存，不能混叫median；no-trade fold保留，observed/eligible/informative fold数分别展示。
- [ ] 默认**建议预注册**门槛（用户未确认前不视为已验证经济标准）：完整observed folds≥4；每key informative folds≥3且每informative fold closed≥10；total closed≥40；usable context coverage=1；至少3个finite PF folds；median fold net expectancy>0；median finite PF>1；正expectancy informative fold占比≥2/3；worst informative fold trade-sequence DD不低于-15%；worst expectancy≥-2%；最大单fold positive net-return-sum占全部positive net-return-sum≤50%。
- [ ] 上述DD/expectancy/集中度阈值是初版保守治理建议，非收益保证。阈值必须在看OOS结果前批准/锁定，所有比较符与min sample配置化并写artifact；不得为pass反向修改。
- [ ] 样本/finite PF/质量/完整性不足 → INSUFFICIENT_DATA；足够而稳定条件失败 → UNSTABLE；全部满足 → STABLE_CANDIDATE。所有reason逐项返回，不输出ENABLED。
- [ ] 写等号、0交易、全胜/全亏、单fold收益集中、UNKNOWN、混version等手算测试。

### Task 2A.5 可复跑CLI与证据持久化

Files：create `scripts/run_walk_forward_stability.py`；modify `backtest/report.py`、`cli.py`、`service.py`对应现有入口；tests `test_walk_forward_artifact.py`。

- [ ] 计划CLI：`python scripts/run_walk_forward_stability.py --config config/walk_forward.yaml --output data/research/walk_forward/<run_id>`；run_id由系统生成/参数显式指定，不手造结果。
- [ ] 保存manifest.json、folds/<fold_id>/{report.json,trades.csv,matrix.csv}、oos_stability.json/csv、methodology.json。每fold可独立复核；failed run独立保存诊断、不发布success latest。
- [ ] manifest包含producer/source config hashes、Python/dependency版本、所有实际消耗行情/benchmark/sector/status/universe/corporate-action文件；不仅十只股票adjusted/raw。
- [ ] 两次同输入运行除timestamp/runtime外结果一致；配置/输入改动可追踪；json无NaN/Infinity；既有report入口同步migration。
- [ ] Python3.11全suite、baseline真实smoke、真实walk-forward；提交代码后干净source生成结果，再单独结果commit，CI验证交付SHA。

### Phase2A验收

覆盖A01–A12；所有禁止项diff检查；fresh物理证据、每折audit invalid/missing/duplicate为0、完整回归CI成功。数据不足允许正确INSUFFICIENT_DATA，不能宣称统计稳定。用户收到完整统计后决定是否开展2B；不自动启用生产策略。

## 4. Phase 2B：证据政策与Dynamic Router

Files：extend `strategy/router.py`与`config/strategy_router.yaml`；create `strategy/enablement.py`、`config/pattern_enablement.yaml`、`tests/test_pattern_enablement.py`；extend `tests/test_scheduler_router.py`、engine/live调用处。

- [ ] 从2A evidence生成待审批policy，不从descriptive `usable_for_router`直接启用。
- [ ] PolicyEntry：四维key、evidence_id/hash、context/config版本、issued_at/valid_from/expiry、enabled/disabled、reason、审批者；默认expiry建议90日，必须审批配置。
- [ ] 精确版本匹配；fail-closed未知/质量差/过期/损坏/未来evidence；PANIC硬NO_TRADE，人工disable优先于统计enable。
- [ ] 回放按当时有效policy，禁止今天最终表回填过去；load原子替换；审计快照与rollback。
- [ ] Router只选Pattern set，保留后续风险限制；扫描器与execution入口都核对授权，不能仅展示隐藏未启用Pattern。
- [ ] B01–B07、真实只读replay smoke、fullsuite/CI。明确policy enable不等于真实执行权限。

## 5. Phase 3：Deterministic Context增强与共享求值

Files：refine `backtest/regime.py`、`market/context.py`、models；create focused `market/features.py`与`tests/test_context_features.py/test_context_adapter_parity.py`（只在现有无合适职责时新增）。

- [ ] 定义as-of Feature/Context snapshot，live/historical adapter只负责取数，同一pure computation负责features/regime/lifecycle。
- [ ] 逐项实现Breadth、leaders、turnover share、persistence、expansion、concentration，记录公式/分母/窗口/最低样本/质量，不把不具备的历史主线数据猜出来。
- [ ] 保持七态/UNKNOWN，信心与质量独立；旧context版本保留可复现读取，不把新算法写进旧版本。
- [ ] 加因果prefix/available_at测试与真实adapter parity，而不是scan同函数两次。
- [ ] C01–C06；新context版本重新2A/2B。若无breadth/sector历史，完成质量降级机制并BLOCKED所需真实验证，不宣称增强有效。

## 6. Phase 4：Pattern Registry / Top Candidate Engine

Files：extend `strategy/signal_engine.py`；create `strategy/pattern_registry.py`、`strategy/candidates.py`；integrate `core/orchestrator.py`；tests `test_pattern_registry.py/test_candidate_ranking.py`。

- [ ] 先将既有Pattern转registry，不改detect/entry/exit参数；对每Pattern做旧新parity，有逻辑变化必须新version并2A重验。
- [ ] Tradable universe→enabled patterns→真实matches→liquidity/RS/theme/risk→ranking；先声明score公式与版本，不拿OOS优化新权重。
- [ ] 输出Candidate snapshot与score breakdown，全部保留完整eligible结果，UI cap10；symbol重复合并规则按最高合格score+Pattern ID deterministic tie-break，保留其他matches证据。
- [ ] LLM仅已有候选risk/context review；structured veto、timeout/error明示，不让错误响应变成新机会。有限veto策略另行审批。
- [ ] D01–D06、全市场真实覆盖manifest、cap/NO_TRADE/重复/排序可重算、CI。

## 7. Phase 5：Live Decision、Alerts与日常调度

Files：extend `market/context.py`、`core/orchestrator.py`、`runtime.py`、`worker.py/worker_state.py`、notifications existing modules；create `strategy/decision_state.py`、tests `test_live_decision_state.py/test_live_replay_parity.py`。

- [ ] finalized bar增量特征、共享Context/evaluator、当前policy、candidate/risk后产生SIGNAL_DECISION，不调用自动broker。
- [ ] 事件状态机持久化WATCH→BUY_READY→BUY_TRIGGERED；只有已确认fill转HOLD；HOLD→SELL_WARNING→SELL_TRIGGERED；任意未成交候选可INVALIDATED；禁止非法跳转并audit。
- [ ] bar重复/乱序/迟到按event id和sequence处理；决策在当时信息快照上追加，不静默重写；stale阻断新entry。
- [ ] alert含上下文、OOS evidence、价格与失效条件；复用现有持久notification queue，分清signal/intent/order/fill去重。
- [ ] 盘前watchlist、盘中更新、收盘daily review接现有交易日/phase scheduler；真实只读session和replay/parity、故障恢复。
- [ ] E01–E07、完整suite/CI；没有真实行情endpoint/凭据只验机制，真实feed验收BLOCKED。行情权限按只读配置，不收集凭据进artifact。

## 8. Phase 6：Paper全闭环与Risk一致性

Files：extend `risk/engine.py`、`execution/engine.py/intent_store.py`、Portfolio/ledger现有接口、`models.py`；tests `test_paper_lifecycle.py/test_risk_contract.py`。

- [ ] 统一风险合同（历史/实时共享可共享规则、账户状态adapter分开），补行业/组合/单日等缺口，不假定已有remote检查等价。
- [ ] Signal→Risk→immutable Intent→Paper order→fills→positions→exit；部分成交、拒单、撤单、资金预留/释放、T+1/board lot/raw costs全部ledger。
- [ ] 重启reconcile、未知回报、重复order保护与audit；现金/持仓不一致阻断新交易。
- [ ] real false、paper endpoint隔离；实时paper观察20交易日/20真实round trips为建议工程门槛，不能合成充数。
- [ ] F01–F07；盈利不是必需工程条件，亏损/滑点/策略衰减如实报告，是否继续由用户决定。

## 9. Phase 7：六大WebUI真实联动

Files：extend `web/app.py`、`market/dashboard.py/workbench.py`、`web/static/index.html/app.js/app.css`；tests extend `test_dashboard.py`、API contract fixtures。

- [ ] 为Dashboard、Candidates、Positions、Backtest、Trade Detail、Strategy Lab补read-only API与一致filters/pagination。
- [ ] 展示Context质量、版本、时间、OOS fold/sample、policy status、UNVERIFIED_CACHE与gross/net单位，no-data/no-trade/failed有不同状态。
- [ ] Trade Detail可追溯snapshot→signal→entry→exit→ledger；Strategy Lab按环境稳定性对比。
- [ ] 后端权限控制、审计control操作；UI不能把real开关当完整授权。
- [ ] G01–G07：启动实际Web服务，浏览器驱动全部页面、真实filters/details/empty/error/narrow layout核验；保存surface证据，不只API或源码字符串。

UI基本read-only查看可随早期artifact接入，但正式Phase7 PASS仍需live/paper链真实整合，不能用早期demo替代。

## 10. Phase 8：有限自动执行（单独授权后）

Files：extend `execution/engine.py`、runtime/config phase permissions、现有MCP/broker adapter层；create broker专用adapter仅在broker契约确认后；tests `test_execution_authorization.py/test_broker_reconciliation.py`。

- [ ] 先明确broker API、sandbox、账户授权、幂等键、query/cancel/fill事件和错误语义，再编码。不能凭空制定可用broker实现。
- [ ] default paper/real false；账户/策略白名单、签署授权、有效OOS/policy、risk、现金/仓位/速率/daily loss限额组合门槛。
- [ ] 首先sandbox全链和fault/reconcile；未知成交查询而不是盲重发；kill switch阻断新单、既有单实况核对。
- [ ] 凭据隔离、redaction、告警、回滚与人工应急演练；生产启用是另一次用户明确操作，不由测试或CI触发。
- [ ] H01–H07。没有真实broker契约/授权则BLOCKED，不能用mock写“自动实盘已完成”。

## 11. Gate、依赖与开发切片

依赖：2A → 2B → 3重跑2A/2B → 4 → 5 → 6 → 7 → 8。2A完整窗preflight与统计模块可在合同冻结后独立开发；execution与report整合由单一owner。2B政策loader与scanner消费必须先共享精确PolicyEntry。后续UI read-only展示可并行，但不得越过生产授权门槛。

每stage验收包必须有：source SHA、禁止项diff、配置/版本hash、physical inputs manifest、完整运行命令/输出、真实smoke、pytest数/skip理由、artifact SHA、CI run/tested SHA、限制及独立结论。报告模板在acceptance文件。

失败/不足不跨阶段：2A机制PASS但样本不足可继续收集，不能进入已授权生产router；2B工程PASS不等于live稳定；paper通过不等于实盘许可。

## 12. 当前交付与下一步

本轮仅写上述三个文档。未修改生产source/配置，未执行Phase2A，未建立/启用policy，未下单。文档中的门槛数字为建议预注册版本，开发前由用户批准后锁定。

交给开发者的第一份任务：只执行Task2A.1–2A.5，按A01–A12提交完整验收包；不修改任何禁止项，不进入2B。验收者先检查fold边界/固定规则/实际物理覆盖，再复跑真实OOS；不得因为既有walk-forward名字存在而认定需求完成。
