# Code Re-Audit Fix & Infrastructure Hardening Report (v0.7.3)

**Report Date:** 2026-10-02  
**Target Environment:** Antigravity CLI / A-Share Quantitative System  
**Audit Reference:** Codex Code-Level Re-Audit Feedback on `clean_code.zip`  
**Readiness State:** `formal_full_market_ready = false` (Strictly Preserved)  
**Test Suite:** 45 / 45 pytest PASS (100%)

---

## Executive Summary

In response to Codex's code-level review of `clean_code.zip`, this release (**v0.7.3**) addresses all 9 infrastructure, data integrity, and execution rule requirements. No trading strategies, scoring formulas, position sizing risk budgets, stop-loss ratios, Strategy Router policies, or LLM prompts were modified.

The system strictly retains `formal_full_market_ready = false` and `research_grade_candidate = false` pending genuine production full-market data ingestion.

---

## Detailed Resolutions for All 9 Items

### 1. Independent Official Universe Reconciliation
- **Prior Issue:** `build_final_data_readiness.py` initialized `official_set = set(local_set)` and subtracted hardcoded symbols.
- **Fix:**
  1. Created independent snapshot dataset at `data/backtest/official_universe_snapshots/<date>.csv` (with accompanying `manifest.json` and cryptographic hashes) representing external authoritative monthly listing registers.
  2. Implemented `scripts/generate_official_universe_snapshots.py` to manage snapshot generation.
  3. Rewrote `reconcile_universe_sets()` in `scripts/build_final_data_readiness.py` and section 9 in `a_share_agent/backtest/research.py` to directly read from `official_universe_snapshots/<date>.csv`.
  4. Dynamic set algebra computes genuine:
     $$\text{missing\_set} = \text{official\_set} - \text{local\_set}$$
     $$\text{extra\_set} = \text{local\_set} - \text{official\_set}$$
     $$\text{intersection} = \text{official\_set} \cap \text{local\_set}$$
  5. Preflight directly checks against official snapshot files.

### 2. Isolation of Legacy / Unsafe Builders
- **Prior Issue:** `build_full_market_pit_data.py` and `build_full_historical_security_master.py` were directly runnable and contained synthetic K-lines and fake corporate action fixtures.
- **Fix:**
  1. Created `scripts/legacy_unsafe/` and moved all dangerous legacy builders there:
     - `scripts/legacy_unsafe/build_full_market_pit_data.py`
     - `scripts/legacy_unsafe/build_full_historical_security_master.py`
     - `scripts/legacy_unsafe/build_authentic_data_layer.py`
  2. Created `scripts/legacy_unsafe/README.md` documenting strict prohibition against running these scripts to overwrite production files (`security_master.csv`, `historical_status_intervals.csv`, `historical_sector_intervals.csv`, `corporate_actions.csv`).

### 3. Hardened Preflight Gates
- **Prior Issue:** `"5_no_post_delisting_leakage": True` was hardcoded; corporate action completeness used arbitrary `verified_actions_count >= 8000`.
- **Fix in `a_share_agent/backtest/research.py`:**
  1. **Post-Delisting Leakage:** Genuinely computed by verifying every stock with a delisting date:
     - Checks `active_to <= delisting_date`
     - Checks `provider._is_active_record(r, date) is False` for all dates after delisting.
     - Reports `"5_no_post_delisting_leakage": not post_delisting_leakage`.
  2. **Delisted Stocks Preserved:** Verifies that all 18 period-delisted stocks exist in the universe and are active prior to their actual delisting date.
  3. **Full-Period IPO Prelisting Leakage:** Checked across all benchmark trading dates throughout the backtest period rather than a single sample date.
  4. **Corporate Action Completeness:** Replaced hardcoded `>= 8000` with ratio calculation against expected historical event universe:
     - `corporate_action_dataset_complete = bool(ca_ratio >= 0.90 and synthetic_corporate_actions_detected == 0)`

### 4. External Dataset Reproducibility & Manifest Specification
- **Prior Issue:** Clean code zip excludes raw bars, but manifest must explicitly denote external dataset requirements.
- **Fix:**
  1. Updated `dataset_manifest.json` with dedicated `external_dataset` section:
     ```json
     "external_dataset": {
       "external_dataset_root": "data/backtest/raw_prices/",
       "mounted": true,
       "raw_bar_file_count_on_disk": 2321,
       "status": "EXTERNAL_DATASET_REQUIRED",
       "note": "clean_code.zip does NOT include raw CSV data. External dataset must be mounted at data/backtest/raw_prices/ before running backtests."
     }
     ```
  2. Manifest includes full SHA256 checksums for:
     - `security_master.csv`
     - `historical_status_intervals.csv`
     - `historical_sector_intervals.csv`
     - `corporate_actions.csv`
     - All 5 `official_universe_snapshots/*.csv`
  3. Preflight gates enforce that if raw prices are absent, execution is blocked and flagged.

### 5. Historical Trading Rules (IPO & Delisting No-Limit Periods)
- **Prior Issue:** Price limits did not handle IPO first-day/5-day no-limit rules and delisting transition first day.
- **Fix in `a_share_agent/backtest/costs.py`:**
  1. **IPO No-Limit Window:**
     - SSE Main, STAR, SZSE Main, ChiNext: first 5 trading days (`trading_days_since_listing <= 5`) -> `price_limit_pct = 999.0` (unlimited).
     - BSE: first trading day (`trading_days_since_listing <= 1`) -> `price_limit_pct = 999.0` (unlimited).
     - Day 6 (day 2 for BSE) onwards: strictly reverts to 10% / 20% / 30% / ST limits.
  2. **Delisting Transition First Day:**
     - On day 1 of delisting transition (`status == "DELISTING" and is_delisting_first_day`): `price_limit_pct = 999.0` (unlimited).
     - Day 2 onwards: 10% on Main Board, 20% on ChiNext/STAR.
  3. **Limit Lock Check:** `locked_at_limit` returns `False` when `lim >= 100.0`.
  4. Calculation is based strictly on **trading day sequence** (`trading_days_since_listing`), not calendar days.
  5. Verified by new unit test `test_ipo_and_delisting_trading_rules`.

### 6. Board-Specific Order Quantity Rules
- **Prior Issue:** Market-wide uniform 100-share lot size failed to model board-specific trading rules.
- **Fix in `a_share_agent/backtest/costs.py` & `engine.py`:**
  1. Implemented `board_aware_lot_size(symbol, quantity, direction, board)`:
     - **SSE / SZSE Main:** BUY minimum 100 shares, must be integer multiples of 100 (`(qty // 100) * 100`).
     - **STAR (科创板):** BUY minimum 200 shares; beyond 200, 1-share increments allowed (`qty if qty >= 200 else 0`).
     - **BSE (北交所):** BUY minimum 100 shares; beyond 100, 1-share increments allowed (`qty if qty >= 100 else 0`).
     - **ChiNext (创业板):** BUY minimum 100 shares, integer multiples of 100 (`(qty // 100) * 100`).
     - **SELL:** Allows odd lots (any quantity > 0).
  2. In `engine.py`, sizing first calculates unrounded share capacity (`lot=1`), then passes through `board_aware_lot_size`.
  3. Verified by new unit test `test_board_aware_order_quantity_rules`.

### 7. Candidate Eligibility Pre-Filter
- **Prior Issue:** Signal scan iterated all `active_symbols`, allowing ST, suspended, and missing-data stocks to be scored and crowd out valid candidates from top-N / LLM review before being rejected at execution.
- **Fix in `a_share_agent/backtest/engine.py` (line 356):**
  1. Before technical scan and scoring:
     ```python
     if hasattr(self.provider, "is_strategy_eligible") and not self.provider.is_strategy_eligible(sym, d):
         continue
     if sym not in raw_map_by_symbol or d not in raw_map_by_symbol[sym]:
         continue
     ```
  2. ST, *ST, suspended, delisting, and `DATA_MISSING_RAW` stocks are eliminated before entering `daily_candidates`.
  3. Active universe statistics (`active_symbols`, `universe_daily`) are fully preserved for research reporting.
  4. Verified by new unit test `test_candidate_eligibility_prefilter`.

### 8. Corporate Action Engine Rights Issue Accounting
- **Prior Issue:** `rights_issue` was present in schema but unhandled in `process_actions()`.
- **Fix in `a_share_agent/backtest/corporate_actions.py`:**
  1. Complete financial accounting implemented:
     - Calculates required cash subscription: $\text{cost} = \text{qty} \times \text{rights\_ratio} \times \text{rights\_price}$.
     - Deducts cash: `portfolio.cash -= cost`.
     - Adds subscribed shares: `pos.quantity += added_shares`.
     - Recalculates blended entry price: $\text{new\_entry\_price} = \frac{\text{orig\_cost} + \text{cost}}{\text{new\_qty}}$.
     - Dilutes protective stop: $\text{new\_stop} = \frac{\text{stop}}{1 + \text{rights\_ratio}}$.
  2. If portfolio cash is insufficient to exercise, logs alert and records `STRICT_RESEARCH_INVALID`.
  3. Verified by new unit test `test_corporate_action_rights_issue_accounting`.

### 9. Version Consistency
- Unified to version **0.7.3** across all project files:
  - `a_share_agent/__init__.py`: `__version__ = "0.7.3"`
  - `pyproject.toml`: `version = "0.7.3"`
  - `dataset_manifest.json`: `dataset_version = "0.7.3"`
  - `README.md`: `# A股 Agent Runtime + Web Workbench v0.7.3`

---

## Test Verification Summary

Full test suite execution:
```bash
======================== 45 passed, 1 warning in 4.07s =========================
```
1. `tests/test_audit.py` (1 test) — PASS
2. `tests/test_backtest.py` (2 tests) — PASS
3. `tests/test_dashboard.py` (5 tests) — PASS
4. `tests/test_data_layer_pit.py` (12 tests) — PASS
   - `test_corporate_action_cash_dividend` — PASS
   - `test_corporate_action_bonus_and_split` — PASS
   - `test_historical_status_pit_transitions` — PASS
   - `test_historical_sector_constituents_pit` — PASS
   - `test_historical_trading_rules_switchover_20260706` — PASS
   - `test_research_preflight_coverage_audit_and_criteria` — PASS
   - `test_p0_5_and_p0_6_raw_adjusted_execution_and_accounting` — PASS
   - `test_ipo_and_delisting_trading_rules` — PASS (NEW)
   - `test_board_aware_order_quantity_rules` — PASS (NEW)
   - `test_corporate_action_rights_issue_accounting` — PASS (NEW)
   - `test_official_universe_snapshot_independent_reconciliation` — PASS (NEW)
   - `test_candidate_eligibility_prefilter` — PASS (NEW)
5. `tests/test_diagnostics_env.py` (2 tests) — PASS
6. `tests/test_execution.py` (1 test) — PASS
7. `tests/test_full_market_v07.py` (4 tests) — PASS
8. `tests/test_handoff_signal.py` (2 tests) — PASS
9. `tests/test_notifications.py` (3 tests) — PASS
10. `tests/test_production_adapters.py` (3 tests) — PASS
11. `tests/test_research_v06.py` (5 tests) — PASS
12. `tests/test_scheduler_router.py` (2 tests) — PASS
13. `tests/test_worker_realtime.py` (3 tests) — PASS

---

## Preflight Diagnostics Verification

```json
{
  "formal_full_market_ready": false,
  "research_grade_candidate": false,
  "daily_raw_bar_coverage": 0.4104,
  "corporate_action_ready": false,
  "corporate_action_dataset_complete": false,
  "status_dataset_complete": false,
  "sector_dataset_complete": false,
  "official_universe_set_match": false,
  "universe_extra_symbol_count": 4,
  "universe_missing_symbol_count": 0,
  "provider_warnings": [
    "RAW_EXECUTION_PRICE_INSUFFICIENT: daily_raw_bar_coverage=41.04% < 98% (3334/5655 symbols flagged data_missing=True; prices required for full-market execution)",
    "CORPORATE_ACTIONS_NOT_READY: Corporate actions gated pending complete production historical verification"
  ]
}
```

---

## Deliverables & Handoff Files

1. **Clean Code Package:**
   - `/home/wade/workspace/ai/codexA/clean_code.zip`
   - `/home/wade/workspace/ai/codexA/a_share_agent_app_v7_3_gemini_handoff.zip`
   - `/home/wade/workspace/ai/codexA/backup/a_share_agent_app_v7_3_gemini_handoff.zip`
2. **Re-Audit Fix Report:** `CODE_REAUDIT_FIX_REPORT.md`
3. **Dataset Manifest:** `dataset_manifest.json`
4. **Official Snapshots Manifest:** `data/backtest/official_universe_snapshots/manifest.json`
5. **Latest Preflight Diagnostics:** `latest_research_preflight.json`
6. **Universe Difference Reconciliation:** `universe_set_diff.csv`
