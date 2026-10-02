# v0.7 Full-Market PIT Validation Report

> 测试完成后复制本文件为 `VALIDATION_REPORT_COMPLETED.md` 并填写。不要删除章节；无数据写 `N/A` 并说明原因。

## A. Environment

- code version:
- git commit:
- test date/time:
- Python version:
- OS:
- Intel MCP endpoint:
- LLM model (if used):
- test period:
- universe mode:
- max_universe:
- pytest result:

## B. v0.7 Change Verification

### B1. Dynamic PIT Universe

- historical universe interface used:
- data source: local security master / MCP interval / MCP daily PIT
- point_in_time_universe_all_days:
- dynamic_universe_daily:
- survivorship_bias:
- fallback_days:
- degraded_days:
- active_universe_min:
- active_universe_avg:
- active_universe_max:
- daily universe hashes present: yes/no
- random date spot checks:

### B2. Historical Sector

- sector_mapping_coverage:
- historical_sector_mapping_coverage:
- sector_history_asof_coverage:
- neutral_sector_trade_share:
- current F10 fallback used in strict run: yes/no

### B3. LLM Structured Output Merge

- structured_output mode:
- api_calls:
- failures:
- error_candidates:
- schema errors:
- avg latency:
- PASS/WATCH/REJECT/ERROR distribution:

## C. Research Preflight

- report path:
- formal_full_market_ready:
- research grade:
- reasons if not Research Grade:
- price_period_coverage:
- benchmark coverage:
- historical sector coverage:

## D. Smoke / Performance

### 50 symbols

- status:
- runtime:
- errors:
- conclusion: interface/performance only; no profit interpretation

### 500 symbols

- first runtime:
- cached rerun runtime:
- peak memory:
- MCP failures:
- missing bars:
- cache hit rates:

## E. Full-Market Deterministic A/B

| Experiment | Return | CAGR | MaxDD | PF | Expectancy | Win Rate | Trades | Gross PnL | Fees | Slippage | Net PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | | | | | | | | | | | |
| no_triple | | | | | | | | | | | |
| core_signal | | | | | | | | | | | |
| router_disabled | | | | | | | | | | | |
| sector_disabled | | | | | | | | | | | |

### E1. By Strategy

- high_volume_breakout:
- single_bull_hold:
- ma_convergence_breakout:
- ma60_breakout_retest:
- ma5_momentum_pullback:
- triple_golden_cross:

### E2. By Market Regime

- risk_on:
- neutral:
- risk_off:

### E3. By Sector Strength / Route

- strong:
- neutral:
- weak:
- most profitable routes:
- most loss-making routes:

## F. LLM Reliability

- experiment id:
- model:
- payload mode:
- candidates reviewed:
- api calls:
- failures:
- error candidates:
- avg call seconds:
- schema errors:
- experiment valid:

## G. LLM A/B

| Metric | Deterministic | LLM Gate | Delta |
|---|---:|---:|---:|
| total return | | | |
| PF | | | |
| expectancy | | | |
| maxDD | | | |
| win rate | | | |
| closed trades | | | |
| avg MAE | | | |
| avg MFE | | | |
| net PnL | | | |

### G1. Decision Audit

#### PASS samples

1.
2.
3.

#### WATCH samples

1.
2.
3.

#### REJECT samples

1.
2.
3.

### G2. Counterfactual outcome of blocked candidates

- blocked winners:
- blocked losers:
- net effect:
- evidence files:

## H. Two-Year Full-Market Results (if executed)

- suite id:
- period:
- research grade:
- total return:
- CAGR:
- benchmark:
- excess return:
- max drawdown:
- PF:
- expectancy:
- monthly return distribution:
- months >= 30%:
- best month:
- worst month:

## I. Validity

- final grade: RESEARCH_GRADE / DIAGNOSTIC_ONLY / INVALID
- reasons:
- known biases:
- data gaps:

## J. Supported Conclusions

1.
2.
3.

## K. Unsupported Conclusions

1.
2.
3.

## L. Recommended Next Experiments

每个实验必须只修改一个主要变量：

### Experiment 1
- id:
- single variable changed:
- hypothesis:
- pass/fail criterion:

### Experiment 2
- id:
- single variable changed:
- hypothesis:
- pass/fail criterion:

## M. Preserved Deliverables

- latest_research_preflight.json:
- feedback_bundle.zip:
- baseline report.json:
- best experiment report.json:
- trades.csv:
- rejections.csv:
- VALIDATION_REPORT_COMPLETED.md:
- git commit:
