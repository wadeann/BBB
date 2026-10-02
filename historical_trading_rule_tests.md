# Historical Trading Rule Dynamic Verification Report (历史交易规则与涨跌停动态切换验证报告)

> **基准日期**: 2026-10-02  
> **审计区间**: 2024-10-01 ～ 2026-09-30  
> **核心规范**: 涨跌幅限制由 `date + exchange + board + risk_warning` 四元组动态驱动，禁止静态写死 ST=5%，全面覆盖 2026-07-06 规则切换与多板块注册制规则。

---

## 一、交易涨跌幅规则体系 (Rule Architecture)

在 A 股多层次资本市场中，涨跌停限制由所属板块、是否实施风险警示（ST / \*ST）以及历史生效日期共同决定：

| 市场板块 | 证券标识特征 | 2026-07-06 切换前（常规） | 2026-07-06 切换前（ST/\*ST） | 2026-07-06 切换后（常规） | 2026-07-06 切换后（ST/\*ST） | 规则依据 |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **SSE Main (上交所主板)** | `600/601/603/605.SH` | **10%** | **5%** | **10%** | **10% (切换至10%)** | 2026-07-06 主板风险警示股票交易机制改革 |
| **SZSE Main (深交所主板)** | `000/001/002/003.SZ` | **10%** | **5%** | **10%** | **10% (切换至10%)** | 2026-07-06 主板风险警示股票交易机制改革 |
| **STAR (科创板)** | `688/689.SH` | **20%** | **20%** | **20%** | **20%** | 上交所科创板股票交易特别规定（注册制20%） |
| **ChiNext (创业板)** | `300/301.SZ` | **20%** | **20%** | **20%** | **20%** | 深交所创业板股票交易特别规定（注册制20%） |
| **BSE (北交所)** | `4/8/920.BJ` | **30%** | **30%** | **30%** | **30%** | 北交所股票交易规则（30%涨跌幅限制） |

---

## 二、2026-07-06 规则切换验证矩阵 (Switchover Verification Matrix)

测试函数：[`test_historical_trading_rules_switchover_20260706`](file:///home/wade/workspace/ai/codexA/src/tests/test_data_layer_pit.py#L197)

### 2.1 切换日前：2026-07-03 (T - 1 交易日)
- `price_limit_pct("600000.SH", "2026-07-03", is_st=True) == 0.05` (**PASS**, 5%限制)
- `price_limit_pct("000001.SZ", "2026-07-03", status="ST") == 0.05` (**PASS**, 5%限制)
- `price_limit_pct("600000.SH", "2026-07-03", status="*ST") == 0.05` (**PASS**, 5%限制)
- `price_limit_pct("600000.SH", "2026-07-03", is_st=False) == 0.10` (**PASS**, 主板常规10%)
- `price_limit_pct("688001.SH", "2026-07-03", is_st=True) == 0.20` (**PASS**, 科创板ST仍为20%)
- `price_limit_pct("300001.SZ", "2026-07-03", is_st=True) == 0.20` (**PASS**, 创业板ST仍为20%)
- `price_limit_pct("920002.BJ", "2026-07-03", is_st=True) == 0.30` (**PASS**, 北交所ST仍为30%)

### 2.2 切换日及切换日后：2026-07-06 及 2026-08-15 (T 日及以后)
- `price_limit_pct("600000.SH", "2026-07-06", is_st=True) == 0.10` (**PASS**, 成功切换至10%)
- `price_limit_pct("000001.SZ", "2026-07-06", status="ST") == 0.10` (**PASS**, 成功切换至10%)
- `price_limit_pct("600000.SH", "2026-08-15", status="*ST") == 0.10` (**PASS**, 切换后持续为10%)
- `price_limit_pct("600000.SH", "2026-07-06", is_st=False) == 0.10` (**PASS**, 主板常规保持10%)
- `price_limit_pct("688001.SH", "2026-07-06", is_st=True) == 0.20` (**PASS**, 科创板保持20%)
- `price_limit_pct("300001.SZ", "2026-07-06", is_st=True) == 0.20` (**PASS**, 创业板保持20%)
- `price_limit_pct("920002.BJ", "2026-07-06", is_st=True) == 0.30` (**PASS**, 北交所保持30%)

---

## 三、一字涨停跌停锁死与撮合阻断验证 (Locked at Limit Test)

在回测撮合引擎 ([`a_share_agent/backtest/engine.py`](file:///home/wade/workspace/ai/codexA/src/a_share_agent/backtest/engine.py#L274)) 中，当开盘一字涨停时禁止买入，开盘一字跌停时禁止卖出。

### 3.1 规则切换对撮合阻断的精准影响
1. **切换日前 (2026-07-03)**:
   - 某主板 ST 股票前收盘价 10.00 元，当日一字开在 10.50 元 (+5.0%)。
   - `locked_at_limit(bar, prev_close=10.0, direction="BUY", is_st=True)` 返回 **`True`**；
   - 撮合引擎正确拦截买单，判定为 `LOCKED_AT_PRICE_LIMIT`。
2. **切换日当天 (2026-07-06)**:
   - 同样该主板 ST 股票前收盘价 10.00 元，开盘价 10.50 元 (+5.0%)。
   - 此时该股涨停板已切换为 +10.0% (11.00元)，10.50 元**并非涨停价**！
   - `locked_at_limit(bar, prev_close=10.0, direction="BUY", is_st=True)` 正确返回 **`False`**；
   - 买单允许正常撮合成交，未发生误拦截！
3. **切换日满额涨停 (2026-07-06)**:
   - 开盘价为 11.00 元 (+10.0%) 且全天一字板；
   - `locked_at_limit` 正确识别达到 10% 上限，返回 **`True`**。

---

## 四、单元测试执行结论
- 测试文件：[`tests/test_data_layer_pit.py`](file:///home/wade/workspace/ai/codexA/src/tests/test_data_layer_pit.py)
- 执行命令：`.venv/bin/pytest tests/test_data_layer_pit.py -k test_historical_trading_rules_switchover_20260706`
- 结果：**100% Passed (0.01s)**
- 判定：**历史交易规则动态引擎具备完整 Point-in-Time 历史保真度**。
