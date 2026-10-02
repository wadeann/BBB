# Full-Market Universe Coverage & Point-in-Time Data Integrity Audit

- **Audit Date**: 2026-10-02
- **Audit Target**: `data/backtest/security_master.csv` & `HistoricalDataProvider`
- **Environment**: Python 3.14.4 / Linux / Local Intel MCP & Astock Kline Cache
- **Audit Conclusion**: `formal_full_market_ready: false` (全市场覆盖率仅约 43.64%，北交所完全缺失，动态 ST/停牌区间与 Sector PIT 成分演变尚未完备)

---

## 1. security_master 分交易所及板块统计表

| 交易所与板块 | 板块代码特征 | security_master 总记录数 | 2026-07-01 active 数 | 2026-08-31 active 数 | 2026-09-30 active 数 | 2026全市场基准(估) | 覆盖率(样本/基准) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SSE Main Board (上交所主板)** | 600/601/603/605 | 828 | 795 | 795 | 794 | ~1,690 | 48.99% |
| **STAR (科创板)** | 688/689 | 422 | 419 | 420 | 419 | ~575 | 73.39% |
| **SZSE Main Board (深交所主板)** | 000/001/002/003 | 787 | 740 | 739 | 738 | ~1,505 | 52.29% |
| **ChiNext (创业板)** | 300/301 | 311 | 307 | 308 | 309 | ~1,355 | 22.95% |
| **BSE (北交所)** | 43/83/87/88/92/.BJ | **0** | **0** | **0** | **0** | ~255 | **0.00%** |
| **合计** | - | **2,348** | **2,261** | **2,262** | **2,260** | **~5,380** | **43.64%** |

---

## 2. 只有 2348 只股票的根本原因调查

1. **直接根源**：`scripts/build_full_market_pit_data.py` 在构建 `security_master.csv` 时，遍历的不是全市场股票花名册，而是 Astock 项目的历史缓存文件 `/home/wade/workspace/ai/Astock/data/kline_cache.json`。
2. **K线替代股票池的严重偏差**：代码中以 `for sym in sorted(clean_kline_map.keys()):` 构造证券主表，直接犯了**用“有本地 K 线缓存的股票数量”替代“全市场股票总数”**的错误。
3. **北交所人为过滤**：在 Astock 原生成脚本 `stock_universe_full.py` 中，存在硬编码过滤逻辑 `[s for s in STOCK_INDUSTRY if not s.endswith(".BJ")]`，导致北交所（63+只）被 100% 抹杀。
4. **规范整改方案**：必须以真实交易所上市名册（含历史已退市标的）作为底表，当某标的行情缺失时，记录为 `tradable=false, data_missing=true`，严禁从 Universe 中物理剔除。

---

## 3. 交易所样本抽查

- **SSE_MAIN (10只)**: `603218.SH`, `600170.SH`, `600032.SH`, `603790.SH`, `600571.SH`, `600499.SH`, `600420.SH`, `600227.SH`, `603730.SH`, `600149.SH`
- **STAR (10只)**: `688398.SH`, `688475.SH`, `688319.SH`, `688052.SH`, `688348.SH`, `688248.SH`, `688018.SH`, `688017.SH`, `688056.SH`, `688125.SH`
- **SZSE_MAIN (10只)**: `000766.SZ`, `002225.SZ`, `002460.SZ`, `000032.SZ`, `002345.SZ`, `000698.SZ`, `002823.SZ`, `002601.SZ`, `002773.SZ`, `002300.SZ`
- **CHINEXT (10只)**: `300760.SZ`, `300310.SZ`, `300832.SZ`, `301558.SZ`, `300428.SZ`, `300004.SZ`, `300189.SZ`, `300768.SZ`, `300560.SZ`, `300182.SZ`
- **BSE (缺失)**: 当前主表无任何 `.BJ` 记录，全量遗漏。

---

## 4. 幸存者偏差与退市股票审计 (2024-10-01 ～ 2026-09-30)

- **期间退市/终止交易股票总数 (`delisted_during_period_count`)**: **18**
- **退市股票样本**:

| 股票代码 | 行业板块 | active_from | active_to | 退市/终止日 | 退市前状态 | 退市当日状态 | 退市次日/当前状态 | 是否剔除出历史 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `000004.SZ` | 综合 | 2024-04-24 | 2026-07-13 | 2026-07-13 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `000016.SZ` | 综合 | 2024-07-23 | 2026-09-03 | 2026-09-03 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `000040.SZ` | 综合 | 2022-11-03 | 2025-03-31 | 2025-03-31 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `000851.SZ` | 综合 | 2023-09-04 | 2025-09-26 | 2025-09-26 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `300379.SZ` | 计算机 | 2023-11-23 | 2026-01-21 | 2026-01-21 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600070.SH` | 综合 | 2023-03-10 | 2025-04-10 | 2025-04-10 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600083.SH` | 综合 | 2022-12-23 | 2025-01-16 | 2025-01-16 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600190.SH` | 综合 | 2023-03-29 | 2025-07-18 | 2025-07-18 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600200.SH` | 综合 | 2023-11-23 | 2025-12-29 | 2025-12-29 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600293.SH` | 建材 | 2024-09-04 | 2026-09-28 | 2026-09-28 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600387.SH` | 综合 | 2023-04-25 | 2025-07-04 | 2025-07-04 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600811.SH` | 综合 | 2023-03-21 | 2025-04-14 | 2025-04-14 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `600837.SH` | 非银金融 | 2022-12-13 | 2025-02-05 | 2025-02-05 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `601198.SH` | 非银金融 | 2024-07-25 | 2026-09-14 | 2026-09-14 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |
| `601989.SH` | 机械 | 2023-07-07 | 2025-08-12 | 2025-08-12 | Active (True) | Active (True) | Inactive (False) | 否 (保留历史) |

---

## 5. 新股 Point-in-Time 样本审计 (2025/2026 上市)

- **2025/2026 上市新股总数**: **30**
- **10 个新股前/当日/后 PIT 验证**:

| 股票代码 | 上市日期 (IPO) | 上市前 30 天检查日 | 上市前 active | 上市当日 active | 上市后 30 天检查日 | 上市后 active | 是否提前泄漏入池 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `001220.SZ` | 2026-02-03 | 2026-01-04 | **False** | **True** | 2026-03-05 | **True** | 否 |
| `001280.SZ` | 2025-12-03 | 2025-11-03 | **False** | **True** | 2026-01-02 | **True** | 否 |
| `001285.SZ` | 2025-09-30 | 2025-08-31 | **False** | **True** | 2025-10-30 | **True** | 否 |
| `001312.SZ` | 2026-04-21 | 2026-03-22 | **False** | **True** | 2026-05-21 | **True** | 否 |
| `001325.SZ` | 2025-12-18 | 2025-11-18 | **False** | **True** | 2026-01-17 | **True** | 否 |
| `001335.SZ` | 2025-04-15 | 2025-03-16 | **False** | **True** | 2025-05-15 | **True** | 否 |
| `001365.SZ` | 2026-05-18 | 2026-04-18 | **False** | **True** | 2026-06-17 | **True** | 否 |
| `001390.SZ` | 2025-05-29 | 2025-04-29 | **False** | **True** | 2025-06-28 | **True** | 否 |
| `001395.SZ` | 2025-01-27 | 2024-12-28 | **False** | **True** | 2025-02-26 | **True** | 否 |
| `001399.SZ` | 2026-06-26 | 2026-05-27 | **False** | **True** | 2026-07-26 | **True** | 否 |

---

## 6. ST 与停牌处理机制审计

1. **引擎层执行机制 (`engine.py`)**:
   - **停牌买入拦截**: 当日无有效 K 线 Bar 时，直接触发 `PRICE_MISSING` 拦截订单，杜绝停牌日买入。
   - **停牌持仓盯市与禁售**: 停牌个股不可撮合卖出，持仓市值维持停牌前最新收盘价计入动态权益。
2. **证券主表层缺陷 (`security_master.csv`)**:
   - 当前主表内全量 2348 行均为 `st=0, suspended=0, tradable=1`，仅为 1 票 1 行静态记录。
   - 缺少真实的 `ST_from/ST_to` 与 `suspension_from/suspension_to` 动态事件区间。虽然 `HistoricalDataProvider` 底层架构原生支持多区间注册（`_register_memberships`），但数据资产本身未填充动态事件表。

---

## 7. Historical Sector Point-in-Time 机制审计

- `sector_membership_point_in_time`: **false**
- `sector_constituent_point_in_time`: **false**

1. **行业归属来源**: 当前主表硬编码行业字段（如 `BK1217, 综合`），由静态字典映射产生。
2. **是否带有效区间**: 当前主表不包含 `effective_from/effective_to` 行业变更区间，为永久单值。
3. **行业变更切换能力**: 当前无动态区间切片，发生主营业务或行业重分类时无法前置切换。
4. **合成行业 OHLCV 的成分股使用**: `scripts/build_full_market_pit_data.py` 采用最新成员静态列表回填整个历史区间，未按每个历史交易日当天的真实成分股动态加权合成。

- **10 只样本公司 × 3 个历史日期检查**:
  - `002088.SZ`: 2024-10-08 (`BK1217 综合`), 2025-06-02 (`BK1217 综合`), 2026-09-30 (`BK1217 综合`)
  - `000506.SZ`: 2024-10-08 (`BK1217 综合`), 2025-06-02 (`BK1217 综合`), 2026-09-30 (`BK1217 综合`)
  - `600036.SH`: 2024-10-08 (`BK0001 制造与科技`), 2025-06-02 (`BK0001 制造与科技`), 2026-09-30 (`BK0001 制造与科技`)
  - `300768.SZ`: 2024-10-08 (`BK1217 综合`), 2025-06-02 (`BK1217 综合`), 2026-09-30 (`BK1217 综合`)
  - `300355.SZ`: 2024-10-08 (`BK0427 电力及公用事业`), 2025-06-02 (`BK0427 电力及公用事业`), 2026-09-30 (`BK0427 电力及公用事业`)
  - `002340.SZ`: 2024-10-08 (`BK0478 有色金属`), 2025-06-02 (`BK0478 有色金属`), 2026-09-30 (`BK0478 有色金属`)
  - `002026.SZ`: 2024-10-08 (`BK0456 家电`), 2025-06-02 (`BK0456 家电`), 2026-09-30 (`BK0456 家电`)
  - `688353.SH`: 2024-10-08 (`BK1037 电子`), 2025-06-02 (`BK1037 电子`), 2026-09-30 (`BK1037 电子`)
  - `001210.SZ`: 2024-10-08 (`BK1217 综合`), 2025-06-02 (`BK1217 综合`), 2026-09-30 (`BK1217 综合`)
  - `603118.SH`: 2024-10-08 (`BK1215 通信`), 2025-06-02 (`BK1215 通信`), 2026-09-30 (`BK1215 通信`)

---

## 8. 价格复权机制与未来信息穿越审计

1. **当前使用方式**: **前复权 (QFQ, Forward-adjusted prices)**。
2. **对信号计算的影响**: 保证移动平均线、突破指标、ATR 无除权除息造成的假跳空。
3. **对执行价格与资金穿越的风险**:
   - 前复权会将未来的送转分红折算因子追溯修改历史价格。
   - 若回测引擎直接以前复权开盘价/收盘价扣除名义现金并计算持仓股数，在历史除权日前将产生名义成交价格失真（例如：历史真实以 20 元买入 1000 股，未来发生 10 送 10 后，历史 QFQ 开盘价变为 10 元，若按 10 元下单导致历史账面与真实交易现金流脱节）。
4. **规范解决方案**:
   - **信号与技术指标**: 使用 Point-in-Time 前复权或全局后复权 (HFQ, Backward-adjusted)。
   - **执行与现金核算**: 必须采用**当时真实除权前成交价格 (Raw/Nominal Unadjusted Prices)**，未来分红/送股作为投资组合的显式除权除息事件处理。

---

## 9. Coverage Audit JSON

```json
{
  "total_security_records": 2348,
  "active_2026_07_01": 2261,
  "active_2026_08_31": 2262,
  "active_2026_09_30": 2260,

  "by_exchange": {
    "SSE_MAIN": 828,
    "STAR": 422,
    "SZSE_MAIN": 787,
    "CHINEXT": 311,
    "BSE": 0
  },

  "universe_market_coverage_ratio": 0.4364,
  "point_in_time": false,
  "dynamic_daily": true,
  "survivorship_bias": false,

  "delisted_during_period_preserved": true,
  "ipo_prelisting_leakage": false,

  "sector_membership_point_in_time": false,
  "sector_constituent_point_in_time": false,

  "price_adjustment_method": "QFQ (Forward-adjusted, potential execution cash-flow lookahead)",

  "formal_full_market_ready": false
}
```
