# Code Fix & Historical Data Authenticity Audit Report (v0.7.2)

**Report Date:** 2026-10-02  
**Target Environment:** Antigravity CLI / A-Share Quantitative System  
**Audit Reference:** Codex Code-Level Audit Feedback on v0.7.1 Handoff  
**Readiness State:** `formal_full_market_ready = false` (Strictly Enforced)

---

## Executive Summary

Following the code-level audit by Codex, this release (**v0.7.2**) completely addresses all identified P0 issues (P0-1 through P0-8). All synthetic bar and artificial corporate action generation routines have been removed from the codebase. The execution engine's unit mismatch between adjusted and raw price spaces has been thoroughly refactored. The Preflight diagnostics module has been stripped of all self-proving defaults, calculating every completeness and coverage metric directly from authentic underlying disk records.

As required by rigorous production data standards, because the authentic raw OHLCV market coverage is currently **41.04%** (2,321 / 5,655 symbols) and authentic corporate actions cover **91 verified events** (1.04% of expected historical volume), the system **strictly sets `formal_full_market_ready = false` and `research_grade_candidate = false`**.

---

## P0 Itemized Resolution Details

### P0-1: Elimination of Synthetic OHLCV Generation
- **Problem Identified by Codex:** `scripts/build_final_data_readiness.py::complete_raw_ohlcv_bars()` generated synthetic OHLCV bars using benchmark returns and symbol hashes whenever real行情 was missing.
- **Actions Taken:**
  1. Completely deleted the synthetic bar generation routines (`complete_raw_ohlcv_bars`, `_generate_synthetic_raw_bars`) from `scripts/build_final_data_readiness.py`.
  2. Physically purged **3,329 synthetic bar JSON cache files** from `data/backtest/cache/bars/` and corresponding synthetic CSVs from `data/backtest/raw_prices/`. Only **2,324 authentic bar files** and **2,321 authentic raw price CSVs** remain.
  3. All 3,334 historical symbols without authentic raw行情 have been flagged in `data/backtest/security_master.csv` as `data_missing=1, tradable=0`. No symbols were physically dropped from the universe.
  4. Real raw price coverage is now computed by actually inspecting files on disk:
     - **SSE Main:** 818 / 1,734 (47.17%)
     - **STAR (科创板):** 420 / 619 (67.85%)
     - **SZSE Main:** 778 / 1,544 (50.39%)
     - **ChiNext (创业板):** 305 / 1,410 (21.63%)
     - **BSE (北交所):** 0 / 348 (0.00%)
     - **Full Market Total:** 2,321 / 5,655 (**41.04%**, failing the ≥98.0% threshold).

---

### P0-2: Purge of Synthetic Corporate Actions
- **Problem Identified by Codex:** `complete_corporate_action_coverage()` used symbol hashes to manufacture ~8,700 artificial cash dividends and bonus splits, falsely tagging them as `CNINFO_EXCHANGE_OFFICIAL_DIVIDEND_REGISTER` and `verified=True`.
- **Actions Taken:**
  1. Completely removed the synthetic dividend/split generation loop from `scripts/build_final_data_readiness.py`.
  2. Purged all 8,698 artificial events from `data/backtest/corporate_actions.csv`.
  3. Retained strictly the **91 authentic, manually/exchange-verified corporate action events** (complete with announcement date, record date, ex-date, payment date, and document ID).
  4. With 91 verified events against an expected historical universe volume of 8,789 events:
     - `corporate_action_source_coverage = 0.0104` (1.04%)
     - `corporate_action_dataset_complete = false`
     - `corporate_action_ready = false`

---

### P0-3: Preflight Self-Proving Removed
- **Problem Identified by Codex:** `a_share_agent/backtest/research.py` assumed `coverage=1.0` if a CSV existed, and hardcoded coverage constants (e.g. 0.9859, 0.9862).
- **Actions Taken:**
  1. Removed all default coverage constants and presence-implies-100% logic from `research.py`.
  2. Dynamic scanning now inspects:
     - Actual `.csv` existence in `data/backtest/raw_prices/` for every universe symbol.
     - Actual provenance headers and `source_url_or_document_id` in `corporate_actions.csv`.
     - Actual event provenance IDs in `provider._status_intervals` (`NOTICE_`, `SUSP_`).
     - Actual reclassification IDs in `provider._sector_intervals_map` (`RECLASS`).
  3. Preflight criteria checklist now strictly checks all 15 operational gates without mock bypasses.

---

### P0-4: Dynamic Set-Level Universe Reconciliation
- **Problem Identified by Codex:** Hardcoded set match results rather than dynamic set difference calculation.
- **Actions Taken:**
  1. Implemented genuine dynamic set algebra in `scripts/build_final_data_readiness.py`:
     ```python
     intersection = local_set & official_set
     missing_symbols = sorted(list(official_set - local_set))
     extra_symbols = sorted(list(local_set - official_set))
     ```
  2. Generated `universe_set_diff.csv` across three historical audit dates (2026-07-01, 2026-08-31, 2026-09-30).
  3. Explicitly recorded discrepancies (e.g., on 2026-07-01: Extra=4 symbols: `600293.SH`, `601198.SH`, `000016.SZ`, `000595.SZ`).
  4. Because `extra_symbols > 0` on 2026-07-01, Preflight reports:
     - `official_universe_set_match = false`
     - `universe_extra_symbol_count = 4`

---

### P0-5: Strict Prohibition of Raw Bar Fallback
- **Problem Identified by Codex:** `HistoricalDataProvider.raw_bars()` fell back to `self.bars(symbol)` when raw行情 was absent, blurring unadjusted and adjusted pricing.
- **Actions Taken:**
  1. Modified `a_share_agent/backtest/data.py::raw_bars()`:
     ```python
     # STRICT: Never fallback to self.bars(symbol). If raw bars do not exist on disk,
     # return empty list to signal DATA_MISSING_RAW.
     self.warnings.append(f"DATA_MISSING_RAW:{symbol}")
     self._raw_bars_mem[symbol] = []
     return []
     ```
  2. Added helper method `adjustment_factor_on(symbol, as_of) -> float` to provide explicit, inspectable split/dividend adjustment ratios when translating price levels across spaces.
  3. In `a_share_agent/backtest/engine.py`, order execution strictly checks `raw_bar`:
     ```python
     if not raw_bar:
         self.rejections.append({"date": d, "symbol": o.symbol, "reason": "DATA_MISSING_RAW", "order": o.to_dict()})
         continue
     ```

---

### P0-6: Unification and Separation of Adjusted vs. Raw Unit Systems
- **Problem Identified by Codex:** Signals generated in adjusted price space were fed directly into raw execution without unit adjustment (e.g., stops in adjusted space applied to raw open prices, and price limits checked against adjusted previous close).
- **Actions Taken in `a_share_agent/backtest/engine.py`:**
  1. **Previous Close for Price Limits:**
     - Computed using the previous day's **raw bar close** (`prev_raw_close`) via `raw_bars_by_symbol` and `raw_dates_by_symbol`.
     - Compared against the current day's `raw_bar["open"]` in pure raw currency units.
  2. **Protective Stop Loss Conversion:**
     - Signal engine calculates `stop_adj` in adjusted space.
     - When generating pending orders, the engine converts this stop into raw execution space:
       $$\text{stop\_raw} = \text{raw\_close} \times \frac{\text{stop\_adj}}{\text{adj\_close}}$$
     - Ensured `stop_raw < raw_open` to prevent inverted risk sizing.
  3. **Risk Sizing and Position Accounting:**
     - Sizing (`portfolio.size_for_risk`) uses `price = raw_open` and `stop = stop_raw`.
     - Sizing and cash deduction operate in authentic raw currency units.
  4. **Intraday Stops & Mark-to-Market:**
     - Intraday stops evaluate raw `high`, `low`, and `open` against `pos.stop_price` (raw).
     - Daily mark-to-market uses `raw_map_by_symbol` close prices.
     - End-of-backtest position liquidation uses raw bar close prices.

---

### P0-7: Status and Sector Provenance Strictness
- **Problem Identified by Codex:** High coverage claims based on synthetic status and sector assignments.
- **Actions Taken:**
  1. Updated `a_share_agent/backtest/research.py` to inspect provenance records:
     - Only intervals with verified document tags (e.g., `NOTICE_`, `SUSP_`, `ANNOUNCEMENT_`) count as verified status provenance.
     - Only intervals with explicit reclassification tags (`RECLASS`) count as verified sector provenance.
  2. Computed honest provenance coverage:
     - `status_source_coverage = 0.0089` (0.89%)
     - `status_dataset_complete = false`
     - `sector_source_coverage = 0.0037` (0.37%)
     - `sector_dataset_complete = false`
  3. Retained schema support (`sector_schema_supports_pit = true`, `status_sample_verified = true`), but barred certifying full-market dataset completeness until production filings are ingested.

---

### P0-8: Dataset Manifest Generation
- **Delivery Requirement:** Output `dataset_manifest.json` detailing row counts, file counts, and cryptographic SHA256 hashes of all underlying data tables.
- **Manifest Details (`dataset_manifest.json`):**
  - `symbol_count`: 5,655
  - `raw_bar_file_count`: 2,323
  - `raw_bar_row_count`: 1,187,093
  - `status_interval_count`: 5,742
  - `sector_interval_count`: 5,676
  - `corporate_action_count`: 91
  - **SHA256 Hashes:**
    - `data/backtest/security_master.csv`: `4861049280624fb65eadba3638c1dd324f028682b8dd3075f416b48d21487115`
    - `data/backtest/historical_status_intervals.csv`: `2e4b992fe7845dd1ef3cc6beebaa9cc9cb64015bb5b75d06b9c5d6d63bdaa902`
    - `data/backtest/historical_sector_intervals.csv`: `946e3402ff365b4ef9d1e4bdd314d8db141a1846326aa01d15c4f8bf8128b6aa`
    - `data/backtest/corporate_actions.csv`: `8cfc424e8d4e51873b96d66dc34798f9ca3220c0939310677bdc24c39ce220a7`

---

## Test Verification & Suite Status

All 40 unit and integration tests across the test suite pass with 100% success:
- `tests/test_audit.py` (1 test) — PASS
- `tests/test_backtest.py` (2 tests) — PASS
- `tests/test_dashboard.py` (5 tests) — PASS
- `tests/test_data_layer_pit.py` (7 tests) — PASS
- `tests/test_diagnostics_env.py` (2 tests) — PASS
- `tests/test_execution.py` (1 test) — PASS
- `tests/test_full_market_v07.py` (4 tests) — PASS
- `tests/test_handoff_signal.py` (2 tests) — PASS
- `tests/test_notifications.py` (3 tests) — PASS
- `tests/test_production_adapters.py` (3 tests) — PASS
- `tests/test_research_v06.py` (5 tests) — PASS
- `tests/test_scheduler_router.py` (2 tests) — PASS
- `tests/test_worker_realtime.py` (3 tests) — PASS

Total: **40 passed, 0 failed, 1 warning** (Starlette DeprecationWarning from FastAPI testclient).

---

## Final Preflight Diagnostics Summary

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

The system strictly adheres to the directive: **no strategy modifications, no backtest runs until full-market authentic historical data ingestion is genuinely complete.**
