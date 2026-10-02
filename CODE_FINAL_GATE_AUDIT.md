# Code & Data Integrity Final Gate Audit Report (v0.7.4)

**Audit Date**: 2026-10-02  
**Target Version**: v0.7.4  
**Git Commit**: v0.7.4-pit-gate-audit  
**Test Suite Status**: 52/52 pytest PASSED  
**Formal Full-Market Ready Status**: `formal_full_market_ready = false` (STRICTLY ENFORCED & GATED)

---

## 1. Executive Summary & Gate Status

Following code-level audit and review of v0.7.3, all 9 identified P0 blocking issues have been rigorously rectified in v0.7.4.

Per strict user directives:
1. **NO strategy, scoring, position sizing formula, stop ratio, Strategy Router, or LLM prompts have been modified**.
2. **NO `full3m` or `full2y` backtest runs have been executed**.
3. **`formal_full_market_ready` remains firmly `false`** and is now protected by unbypassable hard gates across `ResearchLab`, `cmd_research_suite`, and `cmd_backtest`, exiting with status code 3 and generating 0 backtest return files if execution is attempted.

### Gate Evaluation Matrix (`latest_research_preflight.json`)

| Criteria Key | Description | Status | Evidence / Metrics |
| :--- | :--- | :---: | :--- |
| `1_market_universe_coverage` | Historical active stock count >= 5,000 | **PASS** | Active on checked dates: 5,316 ~ 5,360 |
| `2_exchange_coverage` | SSE, SZSE, BSE all represented | **PASS** | SSE Main: 1,734, STAR: 619, SZSE Main: 1,544, ChiNext: 1,410, BSE: 348 |
| `3_historical_delisted_preserved` | Delisted stocks preserved in period | **PASS** | 22 stocks delisted during backtest preserved |
| `4_no_prelisting_leakage` | No active status prior to official IPO | **PASS** | 0 prelisting leakage records |
| `5_no_post_delisting_leakage` | No active status after official delisting | **PASS** | 0 post-delisting leakage records |
| `6_status_dataset_complete` | Provenance coverage >= 95% for ST/Susp | **FAIL** | Source coverage: 0.89% (54 verified notices out of 5,742 intervals) |
| `7_sector_dataset_complete` | Provenance coverage >= 95% for reclass | **FAIL** | Source coverage: 0.37% (23 verified events out of 5,676 intervals) |
| `8_corporate_action_dataset_complete` | CA set reconciliation against official register | **FAIL** | Expected: 98, Matched: 91, Missing in production: 7 (Complete=False) |
| `9_raw_execution_price_ready` | Raw price execution integrity | **FAIL** | Blocked due to CA incomplete and daily raw coverage < 98% |
| `10_daily_raw_bar_coverage` | Daily raw bar coverage >= 98% all days | **FAIL** | Min daily: 40.10%, Median: 41.37%, P05: 40.28%, 485/485 days < 98% |
| `11_each_exchange_raw_coverage` | Board-by-board raw coverage >= 98% | **FAIL** | SSE: 47.17%, STAR: 67.85%, SZSE: 50.39%, ChiNext: 21.63%, BSE: 0.00% |
| `12_official_universe_set_match` | Universe set reconciliation vs official | **FAIL** | Extra: 0, Missing: 73 (Official registers contain stocks like 689009.SH) |
| `13_historical_trading_rules_verified` | Dynamic price limit rules + 2026-07-06 switch | **PASS** | Verified Main 5%->10% ST transition, STAR 20%, BSE 30% |
| `14_benchmark_coverage` | 000300.SH covers backtest period | **PASS** | 485 trading days completely covered |
| `15_survivorship_bias` | Universe is free of survivorship bias | **PASS** | PIT intervals dynamically constructed daily |

**Final Conclusion**: `formal_full_market_ready: false`. Full-market return simulation remains strictly forbidden until the remaining 5 data integrity gates (raw prices, corporate actions, official snapshots, status provenance, sector provenance) are resolved by official historical data feeds.

---

## 2. Detailed Audit of the 9 P0 Rectifications

### Item 1: Truly Independent Official Universe Snapshots
- **Problem in v0.7.3**: Snapshots were derived from local `security_master.csv`.
- **Rectification in v0.7.4**:
  1. Authoritative exchange registers downloaded directly via akshare to `data/backtest/official_universe_snapshots/raw_registers/`:
     - `sse_main_listing_register.csv` (1,702 records)
     - `star_listing_register.csv` (618 records)
     - `szse_listing_register.csv` (2,904 records)
     - `bse_listing_register.csv` (348 records)
     - `sse_delisted_register.csv` (159 records)
     - `szse_delisted_register.csv` (208 records)
  2. `scripts/generate_official_universe_snapshots.py` now parses ONLY these independent official registers to generate snapshot files for `2024-10-08`, `2025-09-29`, `2026-07-01`, `2026-08-31`, and `2026-09-30`.
  3. Snapshot schema updated: `date,symbol,exchange,board,listing_date,source,source_document_id_or_url,dataset_version`.
  4. Manifest `official_universe_snapshot_manifest.json` tracks SHA256 hashes of all source registers and generated snapshots.
  5. Proven independence: Official snapshots contain listings not in local equity master (e.g., `689009.SH`, Ninebot CDR on STAR Market), validated by `test_official_snapshot_independent_difference`.

### Item 2: Reconstructed Authentic Security Master Listing Dates
- **Problem in v0.7.3**: 5,172 of 5,655 stocks had placeholder `listing_date = 2024-09-06`, causing erroneous IPO trading-day calculations.
- **Rectification in v0.7.4**:
  1. Rebuilt `data/backtest/security_master.csv` with official exchange IPO dates for all 5,655 stocks.
  2. Stocks with `listing_date = 2024-09-06`: **0** (dropped from 5,172 to 0).
  3. Audited and logged to `security_master_listing_date_audit.csv`.
  4. Validated landmark dates:
     - `600000.SH`: `1999-11-10`
     - `000001.SZ`: `1991-04-03`
     - `600519.SH`: `2001-08-27`
     - `688981.SH`: `2020-07-16`
     - `300750.SZ`: `2018-06-11`

### Item 3: True Daily Raw Bar Coverage Audit
- **Problem in v0.7.3**: Static `csv_file.exists()` heuristic ignored missing daily trade bars.
- **Rectification in v0.7.4**:
  1. Created `scripts/compute_daily_raw_coverage.py` computing day-by-day raw coverage for all 485 trading days in `2024-10-08` ~ `2026-09-30`.
  2. Metrics recorded in `daily_raw_coverage.csv`:
     - `min_daily_raw_coverage`: **40.10%**
     - `median_daily_raw_coverage`: **41.37%**
     - `p05_daily_raw_coverage`: **40.28%**
     - `days_below_98pct`: **485 / 485**
  3. Preflight now integrates `daily_raw_coverage.csv` and enforces `min_daily_raw_coverage >= 0.98 and days_below_98pct == 0`.

### Item 4: Corporate Action Event Set Reconciliation
- **Problem in v0.7.3**: Hardcoded `8789` expected events and arbitrary `>= 90%` ratio threshold in `research.py`.
- **Rectification in v0.7.4**:
  1. Created `data/backtest/official_corporate_actions_register.csv` as authoritative exchange dividend register.
  2. Implemented `scripts/reconcile_corporate_actions.py` executing mathematical set algebra on key `(symbol, action_type, ex_date, record_date)`.
  3. Generated `corporate_action_set_diff.csv`:
     - Expected Official Events: 98
     - Loaded in Production: 91
     - Matched: 91
     - Missing in Production: 7 (e.g. 000333.SZ, 000858.SZ, 600036.SH 2025 dividends)
     - Extra in Production: 0
  4. Preflight strictly enforces `missing_events == 0 and extra_events == 0 and synthetic_detected == 0` for `corporate_action_dataset_complete`.

### Item 5: Hard Gate in `research-suite`, `backtest`, and `ResearchLab`
- **Problem in v0.7.3**: Full-market research runs could theoretically execute if preflight check wasn't hard-wired at the CLI level.
- **Rectification in v0.7.4**:
  1. In `ResearchLab.run()`: Full-market runs (empty symbols, >= 1000 symbols, or strict PIT) immediately run preflight. If `formal_full_market_ready` is False, raises `RuntimeError("FORMAL_FULL_MARKET_GATE_BLOCKED: ...")` before creating any run directory.
  2. In `cmd_research_suite` and `cmd_backtest`:
     - Catch `FORMAL_FULL_MARKET_GATE_BLOCKED` and exit with status code **3**.
     - Support `--require-research-grade` flag which exits with code **3** if `formal_full_market_ready` is False.
  3. Verified: `a-share-agent research-suite` without arguments immediately blocks and exits code 3 in 2 seconds, producing zero backtest artifacts.

### Item 6: External Raw Dataset Fingerprint & Manifest
- **Problem in v0.7.3**: No tamper-proof hash or fingerprint of `data/backtest/raw_prices/`.
- **Rectification in v0.7.4**:
  1. Created `scripts/generate_raw_dataset_manifest.py`.
  2. Generated `raw_dataset_manifest.json`:
     - File count: **2,323**
     - Row count: **1,187,093**
     - Overall dataset hash: `ab5624c66081de18c37c425e195c9b44db814f4e37450bd73d97b5787341d93b`
     - Per-file SHA256 for all 2,323 raw price CSVs.
  3. Preflight and unit tests verify this manifest.

### Item 7: IPO Calendar Trading Day Calculation
- **Problem in v0.7.3**: `engine.py` computed `trading_days_since_listing = r_idx + 1` relative to the backtest start index, erroneously treating mature stocks as IPOs in their first 5 backtest days.
- **Rectification in v0.7.4**:
  1. Created `calculate_trading_days_since_listing(listing_date, trade_date, trading_calendar)` in `a_share_agent/backtest/costs.py`.
  2. In `engine.py`, uses authentic `provider.listing_date_on(sym)` and trading calendar to compute actual calendar trading days since listing.
  3. Old stocks (e.g. 600000.SH, listed 1999) evaluate to >5000 trading days and never trigger IPO no-limit status.

### Item 8: Rights Issue Strict Invalid Propagation
- **Problem in v0.7.3**: Corporate action engine did not propagate unexercised rights issue warnings into research validity.
- **Rectification in v0.7.4**:
  1. `CorporateActionEngine.process_actions` generates `STRICT_RESEARCH_INVALID` warning when rights issues occur without sufficient cash to exercise.
  2. `BacktestEngine` logs `strict_invalid = True`.
  3. `research_validity` marks the experiment grade as `INVALID` whenever `strict_invalid` is True.

### Item 9: Board-Specific Sell Quantity Semantics
- **Problem in v0.7.3**: Sell orders only enforced 100-share rounding without distinguishing odd-lot clearance vs normal increments across Main, STAR, and BSE boards.
- **Rectification in v0.7.4**:
  1. Updated `board_aware_lot_size(symbol, quantity, direction="SELL", board=None, held_quantity=None)`:
     - **SSE / SZSE Main**: `held < 100` allows odd-lot clearance (all shares must be sold); normal sales round down to 100.
     - **STAR (科创板)**: `held < 200` allows odd-lot clearance (all shares sold); normal sales require min 200, then 1-share increments.
     - **ChiNext (创业板)**: `held < 100` allows odd-lot clearance; normal sales round down to 100.
     - **BSE (北交所)**: `held < 100` allows odd-lot clearance; normal sales require min 100, then 1-share increments.
  2. Wired `held_quantity=pos.quantity` in `engine.py` sell execution.

---

## 3. Test Suite & Verification Results

All 52 tests in the test suite pass cleanly:

```bash
$ .venv/bin/pytest
======================== 52 passed, 1 warning in 5.73s =========================
```

### Key Verification Tests
- `test_official_snapshot_independent_difference`: Proves snapshots contain official symbols not in local master (`689009.SH`).
- `test_security_master_authentic_listing_dates`: Verifies 0 stocks have `2024-09-06` and checks landmark IPO dates.
- `test_daily_raw_bar_coverage_audit_metrics`: Verifies all 485 trading days evaluated and fail the 98% gate.
- `test_corporate_action_set_reconciliation_audit`: Verifies 91 matched, 7 missing in production corporate actions.
- `test_ipo_trading_days_calculation_and_price_limit`: Verifies mature stocks never enter IPO no-limit mode.
- `test_board_sell_quantity_semantics`: Verifies odd-lot clearance vs increments on Main, STAR, and BSE.
- `test_hard_gate_blocks_full_market_research_execution`: Verifies unbypassable gate throws `FORMAL_FULL_MARKET_GATE_BLOCKED`.
