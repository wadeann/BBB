# BBB 阶段验收标准

本文件与 `BBB_PRODUCT_GOALS.md`、`BBB_DEVELOPMENT_PLAN.md` 共同构成外部开发者交付合同。未实现的命令/文件由开发计划明确创建，不得把本标准描述的未来能力当成现有能力。

## 1. 验收角色与结论

开发者提交：source SHA、改动清单、固定配置、测试命令、真实运行产物、运行环境、输入 manifest、已知限制。本助手独立检查代码与调用链、复跑测试/场景、核对 artifact/输入哈希、确认 CI 对应 SHA；不接受仅截图或“已完成”。

结论只有 PASS / FAIL / BLOCKED。统计标签 INSUFFICIENT_DATA/UNSTABLE/STABLE_CANDIDATE 与工程验收结论分开。无法取得必需真实数据是 BLOCKED；伪造、未来泄露、绕过风控、隐藏 skip/错误属于 FAIL。

每阶段必须有：成功路径、缺失/无效输入路径、边界/时间路径、重放一致性、真实数据 smoke、回归 suite、对应提交 CI。无数据不能用合成测试替代真实 smoke；合成 fixture 只用于可控的机制/反例测试。

## 2. 全阶段强制门槛

- 固定 source commit，从干净树运行；artifact 记录真实 producer SHA、配置/规则版本、环境、input hashes。结果提交 SHA 与 producer SHA 可不同；不得手改 producer。
- 所有决策记录 event_time、available_at/as_of、timezone、source quality；最多用 available_at ≤ decision_time 数据。America/UTC 时间统一映射 Asia/Shanghai 交易语义。
- 修改 T 之后价格/数据，不能改变 T 时刻 Context/Pattern/decision；修改尚未可见的公告/财务/板块数据也不能影响 T。
- adjusted/raw 各自绑定物理文件；哈希重算匹配；缺失 raw 禁止执行，禁止自动拿 adjusted 代替。
- 按日对齐 Benchmark/交易日；停牌不能补成可交易；IPO/退市/ST/板块规则与有效日期一致。
- Trade 唯一 attribution：BUY/SELL 对应、缺失/重复 ID fail-closed，signal < executable entry，entry Context 不被 exit 覆盖。
- gross/net/cost、现金/持仓/订单与账本一致；NaN、inf、除零、未闭合交易不能伪造成收益。
- NO_TRADE 可返回，有 reason；0 候选不补足；数据故障与有效 NO_TRADE 状态可区分。
- 默认 paper，allow_real_execution=false。CI、验收、demo 不接真实下单；不得扫描或输出凭据。
- 所有修改的现有契约回归测试；pytest 0 failed/0 collection errors；xfailed 必须是已登记预期问题，不得新增来掩盖回归；skip 列明理由，核心机制测试不得因无 cache 整体跳过。
- CI 必须验证交付 SHA 且 completed/success；不要求 CI 有私有本地行情。真实数据验证在验收机复跑，CI 用 deterministic fixture + schema/合同测试。
- Git 输出/源码/运行证据齐全；只改当前阶段范围，无无关重构或自动执行扩权。

建议通用命令：使用 Python 3.11 隔离 venv 安装 `python -m pip install -e '.[dev]'`；运行 `python -m pytest -q`；`python scripts/run_trading_grade_smoke.py`。安装可能改 tracked egg-info，只允许恢复安装造成的 metadata 变化后从干净 source 生成正式产物，不得恢复用户代码。

## 3. 已完成基础的回归门槛

Phase 1：next executable entry、A 股 T+1、成本/现金、stop/time/signal exit、空 universe、无交易输出、持仓完整清算/标记、gross/net 重算。
Phase 1.5：BUY↔SELL attribution、确定性 ID、entry/exit Context 分离、canonical UNKNOWN、同组统计真实 median、quality fail-closed。
Phase 1.5C：10 请求 symbol 全部 present、adjusted/raw 非空、物理路径/hash/rows 匹配；invalid=0、missing IDs=0、duplicate IDs=[]。既有 27 笔不是所有未来版本必须保持的 magic number；阶段 2A 未改规则/窗口/输入时必须解释任何差异。

## 4. Phase 2A：Walk-Forward OOS Stability

### 必须验证的机制

A01 月历：12/3/3 月窗，train_end < test_start，默认 test 不重叠；测试闰日、跨年、缺交易日、数据不足。配置 step < test 时明确拒绝，第一版不支持重叠 OOS。
A02 独立折：每 fold 独立现金/portfolio/订单；warmup 可来自训练前历史但不得交易或计入 OOS；输出 warmup coverage。
A03 冻结：测试开始前冻结 settings、Pattern/version、features、context规则、threshold配置及 train-derived rule hash；改未来数据只影响其可见后的结果，不重算过去策略。
A04 train/test 边界：训练信号不能作为测试新仓；默认 test 内 signal 且 test 内 execute 才入样；末日不能成交的新买信号不产生虚假 fill。
A05 测试期已有仓位默认零；折末持仓不得因知道 fold_end 强行卖出改善统计。按既有 exit 继续观察至冻结 max holding/已声明 exit horizon，仅管理既有持仓、不产生新仓；成交归属 signal 所在 fold。样本尾部不够观察则 open/censored 独立列示，不纳入 closed expectancy/PF，coverage 报告不能隐瞒。
A06 边界执行：T+1、涨跌停、停牌、无 raw price 等不会得到不可能的 entry/exit；每折归因 audit clean；成本重算一致。
A07 矩阵：key 含四维；missing/UNKNOWN/degraded 独立列示不能偷偷删去；版本不混组；同一 round trip 不重复进 aggregate。
A08 指标：手工可核对 fixture 含胜/负/零收益、偶数 median、全胜/全亏、no trade、连续亏损、不同 holding/MFE/MAE；PF 无亏损用 null + reason，不输出 Infinity 或按任意大值参与 median。
A09 status：阈值前后等号、单折集中收益、最坏折越界、质量不足、缺折/缺交易 → 正确分类；默认门槛见 plan，不得调到真实结果刚好通过。
A10 可复现：重复同 config/input 运行，除 timestamp/runtime外 fold/trade/status/ID 完全一致。哈希改变检测，失败 run 不覆盖 success latest 指针。
A11 正向真数 smoke：所有请求输入 preflight 完整，无关键缺失，至少有一个真实 OOS fold；输出完整 fold/aggregate manifest。要证明 STABLE_CANDIDATE 至少满足预注册 fold/trade门槛；数据不够不能给通过统计标签。
A12 禁止修改：Pattern parameters、signal、router、position sizing、stop、holding、LLM prompts、auto execution；唯一允许接入是研究编排/明确标记 execution window，必须证明既有 engine 普通运行不变。

工程 PASS：A01–A12 + 全局门槛；若只有 1–3 folds，机制可 PASS，统计结论只能 INSUFFICIENT_DATA，不能宣称跨时间稳定。

## 5. Phase 2B：Enablement Policy / Router

B01 只接受通过 2A 的物理绑定证据，status 非 STABLE_CANDIDATE 不启用；人工关闭优先。
B02 精确匹配四维 key、版本、context质量、valid_from、expiry/as_of；未来证据、过期、hash坏、未知组合 → disabled/NO_TRADE，明确 reason。
B03 Train 证据不含当时未来 OOS；回放不能用全历史最终 policy 选择过去交易。政策建立时间之后才能使用。
B04 PANIC、风险硬禁用优先；无证据不能回落默认万能 Pattern。rollback 恢复已知策略快照，不重写历史订单。
B05 Scanner 实际只执行 enabled patterns；未启用 Pattern 即使自身命中也不能生成 BUY_READY。
B06 replay/live 同输入得同 policy selection；表刷新原子替换，中途失败保持完整旧快照或禁用，不能半表。
B07 不增加 broker/auto 路径，audit 记录 evidence/policy hash/拒绝原因。

## 6. Phase 3：Context Enhancement

C01 Regime 七态与 Lifecycle 七态全部 deterministic；相同 as-of prefix 同结果。
C02 Breadth 分母只含当日可用合格股票，不以当前 universe 倒推过去；IPO/退市/停牌/缺数据产生显式 coverage。
C03 leader/turnover/persistence/expansion/concentration 的手算 fixture、边界、零分母与样本不足。
C04 缺必需数据 → UNKNOWN/degraded；confidence 校准含定义及范围，不能拿 confidence 代替quality。
C05 注入未来 sector membership/leader/成交额不改过去 Context；动态 Context 更新不反写 entry snapshot。
C06 新 Context 版本独立跑 2A，再过 2B；旧证据不得无缝授权新版本。更丰富 feature 不等于已证明更有效。

## 7. Phase 4：Candidate Engine

D01 真实匹配 enabled Pattern + 当日 tradability；缺流动性/RS/主题输入 quality fail-closed。
D02 0/3/8/12 matches 输出 0/3/8/10，超过 cap 的未展示者仍可在完整扫描证据查看。
D03 deterministic 排序与 tie-break，跨进程一致；重复 symbol 多 Pattern 合并/并列规则固定，不能重复计算仓位。
D04 score breakdown 可重算，使用证据创建时间 ≤ scan_time；未授权新评分权重不得悄悄调优。
D05 LLM 关闭、timeout、错误响应不产生新标的/Pattern/价格；有限 veto 可记录reason，不能覆盖硬风险禁用。
D06 全市场请求范围/覆盖有真实 manifest；示例10股票不能标全市场成功；扫描耗时/吞吐测量并记录环境，不设无测量依据的承诺。

## 8. Phase 5：Live Decision / Alerts

E01 historical replay 与 live adaptor 输入同 finalized bars，通过同 evaluator 输出相同 signal/context/candidate。
E02 unfinished bar、重复、乱序、迟到、feed outage、重连、跨日/午休均有明确定义；不能当 bar 形成前已知最高/最低。
E03 合法状态转移、禁止转移、revocation/INVALIDATED、跨日恢复；BUY_TRIGGERED≠fill，只有执行回报更新 HOLD。
E04 alert 有 timestamp/as-of、Pattern/version、Context、可执行 entry/stop/invalidation、risk/evidence/原因；幂等重试不重复通知，订单不靠 alert 去重键替代。
E05 stale data 禁止新买入；已有持仓报警显式显示 stale，不悄悄模拟卖出。
E06 真实只读行情接入 session smoke，录像/replay 与断网演练；未提供行情权限则 BLOCKED，不伪造实时数据。
E07 盘前/盘中/收盘 scheduler 与交易日/phase permissions 一致；收盘 review 不改变当时 decision。

## 9. Phase 6：Paper Trading

F01 Signal→Risk→Intent→Order→partial/full fill→Position→Exit 全链；各状态与ledger可对账。
F02 risk 全清单逐项拒绝：停牌、涨停买、跌停卖、已有订单、现金不足、单股/总仓/行业/单笔/组合超限、T+1，含配置边界。
F03 重试/重启/重复回报/乱序回报不重复order/fill；撤单、拒单、partial fill释放/保留资金正确。
F04 raw执行、滑点/成本、lot rounding、corporate action、FIFO或既有lot规则、gross/net/P&L可独立重算。
F05 audit 持久性、重启恢复、断网后 reconcile；持仓/现金/委托不一致禁止新增 intent。
F06 预注册观察门槛：至少20完整交易日，全部7种故障场景（重复事件、乱序、网络断开、进程重启、partial fill、拒单、撤单）通过；真实信号样本不足继续观察，不能注入假成交充数。工程闭环至少20完整 paper round trips；策略收益好坏单独报告，不以盈利作为唯一通过条件。
F07 real execution始终false，broker真实下单调用计数为0。

## 10. Phase 7：WebUI Deep Integration

G01 Dashboard/Candidates/Positions/Backtest/Trade Detail/Strategy Lab 全部数据由真实API提供，无硬编码演示冒充生产。
G02 API与ledger/artifact一致；筛选同时作用于summary/chart/table，pagination不漏或重复，0数据有NO_TRADE/empty-state。
G03 时区、gross/net、price adjustment、sample size、OOS status、quality、UNVERIFIED_CACHE 显式显示；STABLE_CANDIDATE 不标已启用。
G04 Trade Detail完整解释signal→entry→exit→P&L，并保留entry/current Context分离。
G05 Strategy Lab以fold与环境稳定性对比，显式小样本/缺fold；不得默认收益排序伪装验证。
G06 浏览器实际点开六页面、过滤、点trade详情、刷新、错误/空数据、窄屏；截取真实surface并核对API数字。仅source string/单元测试不够。
G07 control操作有auth/permission/audit；无权限不泄漏账号数据，UI开关不能绕过服务端real execution禁用。

## 11. Phase 8：有限自动交易

H01 Phase2A/2B/5/6/7验收PASS，策略授权有效；先broker sandbox全链，再由用户另行明确授权有限实盘。本计划本身不是实盘授权。
H02 默认paper/allow_real_execution=false；缺任一授权/账户白名单/策略白名单/风险上限/有效证据均不可broker order。
H03 Pattern不能直达broker，所有路径经risk/intent审计；手动API/重试/worker同样不能绕过。
H04 dry-run零真实order；限额/订单速率/daily loss/kill switch/reconcile异常均fail-closed；在途单由broker事实确认，kill switch不伪装撤单已成功。
H05 网络超时未知成交不盲重下单；client_order_id幂等；重启broker reconciliation，partial/reject/cancel闭环。
H06 秘钥隔离/redaction、审批、回滚、应急手册演练；部署版本/策略policy可锁定。
H07 明确broker/账户授权与能力（A股规则、订单查询、撤单、回报、sandbox）。缺接口契约只完成可达sandbox工作并标BLOCKED，不用mock宣称真实broker完成。

## 12. 每阶段交付验收报告模板

阶段/范围；source/tested SHA；禁止项diff检查；配置/版本hash；物理输入paths/hash/rows/coverage；scenario命令与原始输出；真实smoke producer/artifact commit；fold/trade/audit/风险数字；pytest通过/失败/skip/xfail；CI run ID/link/conclusion；浏览器/实时/纸交易证据；限制/风险；工程结论与统计结论；是否允许下一阶段（默认不自动进入）。

缺少其中任何适用项，不出PASS。开发者不得把每阶段全部目标缩为scaffold或某个容易通过的子集。
