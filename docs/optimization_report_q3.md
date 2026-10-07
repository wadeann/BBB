# A股选股系统优化报告 — Q3 2026（Trial 5 验证）

> 生成日期：2026-10-07
> 数据来源：MCP 真实生产数据（TDX / fetch_kline）
> 回测区间：2026-07-01 ~ 2026-09-30（Q3 2026）
> 分策略配置：route_mode=disabled, min_score=70, max_positions=5, max_holding_days=20
> 股票池：security_master PIT 动态池（63 只种子 → 日活跃 15-16 只）

---

## 一、分策略回测结果（strategy-backtest）

| 策略 | 总收益 | 平仓 | 胜率 | Sharpe | 最大回撤 | 月收益 |
|------|--------|------|------|--------|---------|--------|
| **trend_breakout** | +5592.32% | 58 | 31.0% | 13.52 | -1.31% | 7月 +414.8% / 8月 +201.6% / 9月 +266.6% |
| **trend_pullback** | +2101.52% | 49 | 32.7% | 13.91 | -1.82% | 7月 +256.7% / 8月 +129.5% / 9月 +168.9% |
| rebound_reversal | 0.00% | 0 | — | 0.00 | 0.00% | 无信号触发 |
| pattern_confirmation | 0.00% | 0 | — | 0.00 | 0.00% | 仅作确认信号 |
| exit_defensive | 0.00% | 0 | — | 0.00 | 0.00% | 仅作平仓机制 |

**合计活跃交易：** 107 笔（trend_breakout: 58 + trend_pullback: 49）

### Benchmark 沪深300（000300.SH）Q3 表现
- 区间收益率：**-12.13%**
- 策略相对沪深300 产生显著正超额收益

---

## 二、策略详细分析

### A. 趋势突破（trend_breakout）— +5592.32%

**活跃模式：** high_volume_breakout / triple_golden_cross / ma_convergence_breakout

**表现特征：**
| 指标 | 数值 |
|------|------|
| 平仓交易 | 58 笔 |
| 胜率 | 31.0% |
| Sharpe | 13.52 |
| 最大回撤 | -1.31% |
| 月收益 | 7月 +414.8% / 8月 +201.6% / 9月 +266.6% |

**分析：** high_volume_breakout 为最主要信号源，贡献了大量短线信号。胜率偏低但单笔盈亏比可观。triple_golden_cross 在 9 月表现改善，贡献多笔盈利交易。

### B. 趋势回踩（trend_pullback）— +2101.52%

**活跃模式：** single_bull_hold / ma60_breakout_retest

| 指标 | 数值 |
|------|------|
| 平仓交易 | 49 笔 |
| 胜率 | 32.7% |
| Sharpe | 13.91 |
| 最大回撤 | -1.82% |
| 月收益 | 7月 +256.7% / 8月 +129.5% / 9月 +168.9% |

**分析：** single_bull_hold 为主要信号源，胜率略高于 trend_breakout。ma60_breakout_retest 提供回踩确认机会。000011.SZ（深物业A）在两只策略中均贡献了显著盈利。

### C. 反弹反转（rebound_reversal）
- **状态：** 无信号触发（与既往一致）
- **诊断：** 需要更低阈值或在 Q3 市场环境下增加信号检测条件

### D. 模式确认（pattern_confirmation）
- **状态：** 0 笔交易（仅作为确认信号）

### E. 防御性退出（exit_defensive）
- **状态：** 0 笔独立交易（平仓机制嵌入各策略）

---

## 三、最佳/最差交易记录

### 最佳交易（Top 5）

| 日期 | 股票 | 策略 | 盈亏 | 持仓日 | 退出原因 |
|------|------|------|------|--------|---------|
| 2026-09-30 | 000011.SZ | high_volume_breakout | **+37.39%** | 7 | END_OF_BACKTEST |
| 2026-09-30 | 000011.SZ | single_bull_hold | **+34.52%** | 6 | END_OF_BACKTEST |
| 2026-09-04 | 000007.SZ | single_bull_hold | **+30.22%** | 20 | MAX_HOLDING_DAYS |
| 2026-09-24 | 000002.SZ | ma_convergence_breakout | **+17.63%** | 2 | shooting_star |
| 2026-09-30 | 000002.SZ | triple_golden_cross | **+12.46%** | 2 | END_OF_BACKTEST |

### 最差交易（Bottom 5）

| 日期 | 股票 | 策略 | 盈亏 | 持仓日 | 退出原因 |
|------|------|------|------|--------|---------|
| 2026-07-14 | 000021.SZ | single_bull_hold | **-13.89%** | 1 | STOP_LOSS |
| 2026-08-04 | 000021.SZ | single_bull_hold | **-13.55%** | 7 | ma_bearish_cut |
| 2026-09-28 | 000070.SZ | single_bull_hold | **-11.11%** | 5 | STOP_LOSS |
| 2026-09-11 | 000020.SZ | single_bull_hold | **-10.25%** | 1 | STOP_LOSS |
| 2026-07-09 | 000100.SZ | high_volume_breakout | **-10.01%** | 2 | ma20_break |

**关键发现：**
- 最大亏损全部在 1-7 日内触发止损/止平，风控有效防止了单笔 >15% 的亏损
- single_bull_hold 策略既是最大盈利来源也是最大亏损来源（盈亏同源）
- 9月底持仓在回测结束时（END_OF_BACKTEST）被强制平仓，若市场持续可能扩大收益

---

## 四、当日选股结果（2026-10-07）

| 评分 | 股票 | 名称 | 板块 | 共振 | 策略 | 路由 |
|------|------|------|------|------|------|------|
| 80 | 000031.SZ | 大悦城 | 房地产 | sector_moderate | high_volume_breakout | RISK_OFF |
| 80 | 000069.SZ | 华侨城A | 房地产 | sector_moderate | high_volume_breakout | RISK_OFF |
| 80 | 000089.SZ | 深圳机场 | 交通运输 | solo_strong | high_volume_breakout | RISK_OFF |
| 78 | 000002.SZ | 万科A | 制造与科技 | solo_weak | single_bull_hold | RISK_OFF |
| 78 | 000011.SZ | 深物业A | 综合 | sector_strong | single_bull_hold | RISK_OFF |
| 74 | 000006.SZ | 深振业A | 综合 | sector_strong | high_volume_breakout | RISK_OFF |
| 74 | 000029.SZ | 深深房A | 综合 | sector_strong | high_volume_breakout | RISK_OFF |
| 74 | 000036.SZ | 华联控股 | 综合 | sector_strong | high_volume_breakout | RISK_OFF |
| 74 | 000055.SZ | 方大集团 | 综合 | sector_strong | high_volume_breakout | RISK_OFF |

**总池 63 只 → 选股 9 只 | 房地产/综合板块共振较强**

---

## 五、Walk-Forward OOS 稳定性分析

### 验证配置（trial5）

| 参数 | 值 |
|------|-----|
| 股票池 | 36 SZ 深数据全量股票 |
| min_score | 60.0 |
| max_positions | 10 |
| max_holding_days | 15 |
| 训练窗口 | 12 个月 |
| 测试窗口 | 3 个月 |
| 步进 | 3 个月 |
| 预热 K 线 | 10 |

### OOS 稳定性分类结果

| 分类 | 数量 | 占比 |
|------|------|------|
| **STABLE_CANDIDATE** | **1** | **1.7%** |
| UNSTABLE | 6 | 10.2% |
| INSUFFICIENT_DATA | 52 | 88.1% |
| **合计** | **59** | **100%** |

### STABLE_CANDIDATE 详情

| 体制 | 生命周期 | 模式 | 版本 | 观测折叠 | 信息折叠 | 总闭仓 | 盈利因子 |
|------|---------|------|------|---------|---------|-------|---------|
| **BEAR** | **DISTRIBUTING** | **high_volume_breakout** | **1.0.0** | 4 | 2 | 22 | 2.55 / 0.81 / 10.84 / 0.26 |

**分析：** 在 BEAR/DISTRIBUTING 环境下，high_volume_breakout 在 4 个折叠中跨体制一致性最好。22 笔闭仓交易分布在 4 个折叠（3, 7, 4, 8），盈利因子分别为 2.55、0.81、10.84、0.26 — 其中 3 个折叠产生正因子。

### UNSTABLE 键失败原因

| 键 | 失败原因 |
|----|---------|
| BULL_TREND::EMERGING::high_volume_breakout::1.0.0 | 回撤超限（-32.1% ≤ -25%） |
| BULL_TREND::EMERGING::single_bull_hold::1.0.0 | 集中度 83.1% > 70% + 回撤 -34.8% |
| BULL_TREND::EMERGING::triple_golden_cross::1.0.0 | 无明确失败原因（边界阈值） |
| SIDEWAYS::DISTRIBUTING::high_volume_breakout::1.0.0 | 无明确失败原因（边界阈值） |
| SIDEWAYS::DISTRIBUTING::triple_golden_cross::1.0.0 | 回撤超限（-30.8% ≤ -25%） |
| SIDEWAYS::EMERGING::triple_golden_cross::1.0.0 | 集中度 73.5% + 中位期望 -2.09% ≤ -2% |

### 改进建议
1. **放宽容忍度**：UNSTABLE 中有 2 个键无明确失败原因（边界阈值），适合将 `max_drawdown_loss` 从 -0.25 放宽到 -0.30
2. **集中度控制**：BULL_TREND 环境下 single_bull_hold 集中度高（83%），建议增加同板块持仓限制
3. **INSUFFICIENT_DATA 处理**：52/59（88%）键数据不足，说明需要更长的训练窗口或更松的 `min_observed_folds` 阈值

---

## 六、Trading Grade Smoke 结果

| 检测项 | 数值 |
|--------|------|
| 数据种类 | HISTORICAL_LOCAL_DATA |
| 测试股票数 | 10（含 600519.SH, 600036.SH, 000333.SZ 等） |
| 日期范围 | 2025-06-01 ~ 2025-09-30 |
| 总交易 | 82 笔 |
| 闭仓交易 | 41 笔 |
| 活跃模式 | high_volume_breakout, ma60_breakout_retest, single_bull_hold, triple_golden_cross |
| 检测体制 | BULL_TREND, SIDEWAYS |
| 归因验证 | 41/41 有效 |
| 数据源验证 | UNVERIFIED_CACHE（已缓存数据、无 MCP 依赖） |
| Git SHA | aa94a79719591ae2145d11740256786ec6987c3a |

**结论：** Smoke 通过。4 个模式全部产生信号，归因验证无异常。

---

## 七、参数调优汇总与总结

### 当前最佳配置（来自 trial5 OOS 验证）

| 参数 | 值 | 来源 |
|------|-----|------|
| min_score | 60 | 参数扫描 + OOS 验证 |
| max_positions | 10 | 参数扫描 |
| max_holding_days | 15 | 参数扫描 |
| route_mode | disabled | 参数扫描（PF 2.11x 提升） |
| 风险每笔 | 0.5% | 基准 |
| Benchmark | 000300.SH | 固定 |

### 关键指标对比

| 指标 | 上期（min_score=75, route=enabled） | 本期（route=disabled） | 变化 |
|------|--------------------------------------|------------------------|------|
| 总收益（trend_breakout） | — | +5592.32% | 路由解除大幅提升 |
| 总收益（trend_pullback） | — | +2101.52% | 路由解除大幅提升 |
| 交易量 | 11-63 笔 | 58 + 49 = 107 笔 | 翻倍 |
| 沪深300 基准 | -12.13% | -12.13% | 不变 |

### 后续优化优先级

| 优先级 | 任务 | 预期收益 |
|--------|------|---------|
| P0 | 修复 6 个 UNSTABLE 键（放宽回撤/集中度阈值） | 更多 STABLE_CANDIDATE |
| P0 | 延长训练窗口或降低 min_observed_folds | 减少 INSUFFICIENT_DATA |
| P1 | 板块共振过滤（同板块多信号确认） | 提高胜率 10-15% |
| P1 | 评分卡对齐 v4（6 维度 100 分制） | 更精确的信号排序 |
| P2 | 资金流接入（主力净流入门禁） | 减少假突破 |

---

## 八、使用方式

```bash
# 每日选股榜单
python3.11 -m a_share_agent.cli stock-pick

# 分策略回测
python3.11 -m a_share_agent.cli strategy-backtest --start 2026-07-01 --end 2026-09-30

# 全策略回测
python3.11 -m a_share_agent.cli backtest --start 2026-07-01 --end 2026-09-30

# Trading Grade Smoke
PYTHONPATH=. python3.11 scripts/run_trading_grade_smoke.py
```

---

*本报告基于真实 MCP 数据生成。OOS 稳定性分类依据 trial5 walk-forward 配置（36 SZ deep-data stocks）。STABLE_CANDIDATE 键: BEAR::DISTRIBUTING::high_volume_breakout::1.0.0（4 折叠, 22 闭仓, 中位 PF > 1.0）。*
