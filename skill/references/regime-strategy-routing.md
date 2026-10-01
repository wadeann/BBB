# Regime Strategy Router v4

## 1. 为什么需要路由
同一套形态在不同大盘、情绪与板块生命周期下，期望值可能完全不同。因此 v4 不再只做“市场加减分”，而是在个股评分前先决定**哪些策略族现在有资格参与**。

## 2. 环境维度

### 大盘风险
- `risk_on`：可承担正常风险预算；
- `neutral`：提高门槛、降低仓位；
- `risk_off`：默认禁止新开高β策略。

### 大盘趋势
`up / range / down / unknown`，由配置的多个宽基指数趋势共同生成，不能只看单一指数。

### 情绪阶段
`warming / hot / divergent / cooling / panic / rebound / unknown`。
推荐由 market_health、limitup_ladder、mainline_lanes、炸板率、连板高度和广度共同判断。

### 板块强度
`strong / neutral / weak / unknown`。

### 板块生命周期
- `emerging`：刚形成共振；
- `accelerating`：趋势与资金扩散；
- `crowded`：一致性过高、拥挤；
- `cooling`：强度下降；
- `unknown`。

## 3. 策略族

### trend_breakout
三线金叉、均线收敛突破、巨量突破。
最佳环境：risk_on + 强板块；neutral 时提高阈值；risk_off/弱板块默认阻断。

### trend_pullback
MA60突破回踩、MA5强势回踩、单阳不破、地量小阳。
比突破类更适合 neutral 环境，但仍要求结构没有破坏。

### rebound_reversal
龙回头、反包等。
只在独立“反转确认”规则成立时运行；不能把暴跌后的第一次反弹直接当成买点。

### pattern_confirmation
长阳七星等时间/形态模式。默认只作确认或加减分，不可单独触发订单。

### exit_defensive
所有退出、减仓与风险保护策略。在任何 regime 下始终允许。

## 4. 路由顺序
1. 若 `risk_off` → RISK_OFF 最高优先级；
2. 若板块 `weak` → 强制弱板块规则；
3. 特定 `panic/rebound` 情况才允许反转策略例外；
4. 其余按 market + sector 精确匹配；
5. 无匹配或关键状态 unknown → 新开仓默认阻断或降级人工复核。

## 5. 路由不是“猜行情”
路由只根据可观察状态决定：
- 哪类策略可以运行；
- 评分门槛提高多少；
- 仓位乘数；
- 单日最大新仓数量。

它不能：
- 保证某个状态持续；
- 因“看好后市”跳过风控；
- 为达到收益目标扩大风险。

## 6. 回测要求
至少比较：
- 无路由基线；
- 仅 market gate；
- market + sector gate；
- market + sector + strategy router。

按 regime 分层报告每个策略族的样本数、期望值、盈亏比、最大回撤、MFE/MAE 和滑点。参数只有在样本外表现稳定后才能晋级生产。
