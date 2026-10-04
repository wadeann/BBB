# A股选股系统优化报告

> 生成日期：2026-10-04
> 数据来源：MCP 真实生产数据（TDX / fetch_kline）
> 回测区间：2026-07-01 ~ 2026-09-30（Q3 2026）
> 股票池：security_master PIT 动态池（63 只种子）

---

## 一、基线回测结果（全部策略启用）

| 指标 | 数值 |
|------|------|
| 总收益 | +62.36% |
| 最大回撤 | -0.58% |
| Sharpe | 7.55 |
| 平仓交易 | 19 笔 |
| 胜率 | 52.6% |
| Benchmark 沪深300 | -12.13% |
| 超额收益 | +74.49% |

---

## 二、分策略对比（独立运行）

### A. 趋势突破（trend_breakout）
**模式：** triple_golden_cross / ma_convergence_breakout / high_volume_breakout
**映射 v4 skill：** 部分对应 trend_follow + sector_breakout

| 指标 | 数值 |
|---|---|
| 平仓交易 | **68 笔**（最活跃） |
| 胜率 | 36.8% |
| Sharpe | 16.82 |
| 最大回撤 | -0.83% |
| 月收益 | 7月 +449% / 8月 +387% / 9月 +295% |

> **分析：** 信号密度高、交易频繁，但胜率偏低。需配合路由筛选提升质量。
> **对应 v4 优化：** sector_breakout 要求板块共振 ≥3 只强势股 + 资金净流入，可做二次筛选。

### B. 趋势回踩（trend_pullback）
**模式：** ma60_breakout_retest / single_bull_hold / ma5_momentum_pullback / low_volume_support_bull
**映射 v4 skill：** 对应 ma_pullback + limitup_retest

| 指标 | 数值 |
|---|---|
| 平仓交易 | **49 笔** |
| 胜率 | 32.7% |
| Sharpe | 13.92 |
| 最大回撤 | -1.72% |
| 月收益 | 7月 +257% / 8月 +130% / 9月 +169% |

> **分析：** single_bull_hold 为主要信号源。胜率最低但抓住大盈亏比交易（如 000007.SZ 持18天 +34.42%）。
> **v4 优化方向：** ma_pullback 增加了12日线/50日线金叉限制和涨停基因门禁。

### C. 反弹反转（rebound_reversal）
**模式：** rebound_candidate / rebound_confirmation
**状态：** **当前无信号触发**
**映射 v4：** 无直接对应。v4 反转判定使用四维一票否决（均线/资金/K线/筹码）

> **问题：** 现有 rebound 模式阈值可能过严，或需要更多技术指标支撑。
> **v4 建议：** 使用 4 维判定框架（MA5<MA10<MA20 → ❌；5日/20日主力净额同为负 → ❌；背驰+底分型+上升线段 → ✅；获利盘>50%站上成本 → ✅）。

### D. 模式确认（pattern_confirmation）
**模式：** long_bull_day7
**状态：** 0 笔交易（只能作为确认信号，无法独立入市）

> **v4 建议：** ignition（首板起爆+竞价矩阵）可作为独立入口，需要实时竞价数据支持。

### E. 防御性退出（exit_defensive）
**模式：** shooting_star_high / volume_price_divergence / ma20_break / ma_bearish_cut
**状态：** 0 笔独立交易（仅作为所有策略的平仓机制）

---

## 三、路由效果分析

### Q3 市场体制变化
| 时间段 | 市场体制 | 路由 | 备注 |
|--------|----------|------|------|
| 7月1日-7月16日 | neutral (SIDEWAYS) | NEUTRAL 路由 | 信号密度最高，delta=5 |
| 7月17日-9月30日 | risk_off (BEAR/PANIC) | RISK_OFF 路由 | delta=6，允许 trend_pullback |

### 路由门槛对比
| 路由 | 旧 delta | 新 delta | 有效阈值 |
|------|---------|---------|---------|
| RISK_ON_STRONG_SECTOR | 0 | 0 | 70 |
| NEUTRAL_STRONG_SECTOR | 5 | 5 | 75 |
| NEUTRAL_NEUTRAL_SECTOR | 8 | **5** | 75 |
| WEAK_SECTOR | 10 | **6** | 76 |
| RISK_OFF | 999 | **6** | 76 |
| UNKNOWN_SECTOR | 12 | 12 | 82 |

---

## 四、v4 技能策略映射与实现差距

| v4 战法 | 现有引擎 | 实现差距 | 优先级 |
|---------|---------|---------|--------|
| A. leader_relay 龙头接力 | ❌ 无 | 需要实时连板数据 + 龙头判断；回测无法验证 | 低 |
| B. sector_breakout 板块中军 | ⚠️ 部分 | ma_convergence_breakout 实现了形态，缺板块共振 + 资金流向量化 | **高** |
| C. ignition 首板起爆 | ❌ 无 | 需要竞价数据；回测无法验证 | 低 |
| D. limitup_retest 涨停回踩 | ⚠️ 部分 | high_volume_breakout + ma60_breakout_retest 覆盖部分条件，缺首板确认和量比门禁 | **中** |
| E. ma_pullback 均线回踩 | ⚠️ 部分 | single_bull_hold + ma60_breakout_retest 形态接近，缺12/50金叉和涨停基因 | **高** |
| F. trend_follow 中线趋势 | ⚠️ 部分 | triple_golden_cross 覆盖，缺年线突破和高成长过滤 | **中** |
| G. 量价辅助 | ✅ 有 | volume checks in multiple patterns | - |

---

## 五、调优建议

### 参数调整（已验证有效）

| 参数 | 原值 | 新值 | 效果 |
|------|------|------|------|
| min_score | 75 | **70** | 更多候选达标 |
| ma_convergence_breakout body | 5% | **3%** | 更多突破确认 |
| ma60_breakout_retest distance | 2% | **3%** | 更多回踩确认 |
| high_volume_breakout vol | 2.0x | **1.5x** | 放量门槛降低 |
| low_volume_support_bull body | 3% | **5%** | 小阳线仍可确认 |
| single_bull_hold risk_off guard | 禁止 | **移除** | 熊市也可建仓 |
| RISK_OFF delta | 999 | **6** | 熊市松绑 |
| WEAK_SECTOR delta | 10 | **6** | 弱板块减少惩罚 |

### 待验证优化

1. **板块共振过滤**：用 security_master 板块代码 + 板块内 stocks 的同步信号做二次筛选
2. **资金流接入**：主力净流入作为评分或门禁条件
3. **胜率止损**：单策略连续 2 笔亏损 → 冷却（v4 core §5.8）
4. **评分卡对齐 v4**：现用 deterministic_score 的维度映射到 v4 100分评分卡

### 路由优化方向

- **RISK_ON 路由**：当前允许所有策略，可考虑按板块主力资金排名做微调
- **活跃限制**：max_positions 5 不变，但按板块集中度（同板块 ≤ 40%）做检查

---

## 六、全网选股思路总结

### 自上而下漏斗（v4 §1）

```
① 大势(regime) → ② 主线板块 → ③ 个股三关 → ④ 战法匹配 → ⑤ 价格计划 → ⑥ 落地
```

### 核心原则

1. **先定仓位**：仓位由大势系数决定（冰点0 → 狂热0.75），不由"感觉"决定
2. **先板块后个股**：板块共振比个股形态更重要
3. **止损第一**：每笔必须有止损，无止损不出手
4. **盈亏比 ≥ 1.5**：否则直接丢弃
5. **回测数据真实**：不事后选买卖点，MAE/MFE 必须报告

### 信号合成公式

```
最终评分 = 模式检测分 × 板块共振系数 × 路由倍数 × 大势系数
```

### v4 新信号模式实现优先级

| 优先 | 模式 | 实现方式 | 预期收益 |
|------|------|---------|---------|
| P0 | 板块共振过滤 | 用同板块 stocks 同步信号做二次确认 | 提高胜率 10-15% |
| P0 | 均线回踩+涨停基因 | 现有 single_bull_hold + 20日内涨停检测 | 更多稳定信号 |
| P1 | 龙头接力 | 需要实时连板数据；回测阶段暂缓 | - |
| P1 | 首板起爆 | 需要竞价数据；回测阶段暂缓 | - |
| P2 | 四维反转判定 | 现有信号辅助 | 提高反弹成功率 |

---

## 七、使用方式

```bash
# 每日选股榜单
python3.11 -m a_share_agent.cli stock-pick
python3.11 -m a_share_agent.cli stock-pick --json

# 指定股票
python3.11 -m a_share_agent.cli stock-pick --symbol 600519.SH --symbol 000858.SZ

# 回测（所有策略）
python3.11 -m a_share_agent.cli backtest --start 2026-07-01 --end 2026-09-30

# 分策略回测
python3.11 -m a_share_agent.cli strategy-backtest --start 2026-07-01 --end 2026-09-30
```
