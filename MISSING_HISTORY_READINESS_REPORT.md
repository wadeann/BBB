# Missing History Readiness Report

**Generated At**: 2026-10-02T20:40:00+08:00  
**Audit Scope**: v0.7.8 Missing History Readiness Audit (Membership Readiness vs. Execution Readiness)

---

## 1. Git State

- **Branch**: `master`
- **HEAD Commit**: `f21e5127753178442812b60ffb3f45e6921f5099` (`Merge pull request #11 from wadeann/fix/v078-missing-history-readiness`)
- **Recent Commits**:
  - `f21e512` Merge pull request #11 from wadeann/fix/v078-missing-history-readiness
  - `1a02dcb` test: bind committed missing-history readiness split
  - `0b326f9` test: distinguish PIT membership readiness from execution readiness
  - `10e6a1b` feat(data): add missing-history readiness audit CLI
  - `275e9d2` feat(data): add fail-closed missing-history readiness audit
- **Working Tree**: Clean (Auditing-only, no business/strategy code modifications)

---

## 2. Tests

- **Command**: `pytest -q`
- **Result**:
  - `passed`: 74
  - `failed`: 0
  - `xfailed`: 1 (`test_strict_research_gate_blocks_incomplete_production_actions`)
  - `warnings`: 1 (StarletteDeprecationWarning regarding httpx testclient)
  - `runtime`: 7.32s
- **Status**: 100% Passed / 0 Errors

---

## 3. Universe

- **Snapshot Count**: 5 snapshots (2024-10-08, 2025-09-29, 2026-07-01, 2026-08-31, 2026-09-30)
- **Legacy Schema Snapshots**: 0
- **Missing Observations Total**: 59
- **Unique Missing Symbols**: 35
- **Extra Observations Total**: 0
- **Unique Extra Symbols**: 0
- **Official Universe Source Semantics Verified**: `False` (Pending complete SSE delisting date resolution across all register records)
- **Detailed Diff File**: `official_universe_set_diff_detailed.csv`

---

## 4. Candidate Membership Readiness

- **Candidate Count**: 35
- **Membership Ready Count**: 17
- **Membership Blocked Count**: 18

> **Definition**: `membership_ready` indicates that independent exchange register records contain explicit listing and termination dates with verified source semantics. It represents reference-data PIT membership readiness only and does **not** grant strategy trading eligibility.

### Membership-Ready Symbols (17 SZSE Stocks)

All 17 stocks originate from `szse_delisted_register.csv` with explicit `终止上市日期` (`EXPLICIT_TERMINATION_FIELD`):

| Symbol | Listing Date | Delisting Date | Source File | Source Semantic Status | Membership Ready | Execution Ready | Blockers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `000584.SZ` | 1995-11-28 | 2025-07-11 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `000622.SZ` | 1996-11-07 | 2025-07-16 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `000627.SZ` | 1996-11-12 | 2025-09-30 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `000638.SZ` | 1996-11-26 | 2026-06-03 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `002231.SZ` | 2008-05-12 | 2026-03-27 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `002336.SZ` | 2010-01-13 | 2025-07-04 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `002750.SZ` | 2015-03-24 | 2025-06-27 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `002808.SZ` | 2016-08-12 | 2026-07-14 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `002898.SZ` | 2017-09-12 | 2026-07-17 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300029.SZ` | 2009-12-25 | 2026-07-10 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300108.SZ` | 2010-08-25 | 2025-05-29 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300117.SZ` | 2010-09-02 | 2025-04-30 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300208.SZ` | 2011-04-26 | 2025-07-21 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300280.SZ` | 2011-12-29 | 2025-10-14 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300344.SZ` | 2012-08-01 | 2026-04-22 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300391.SZ` | 2014-08-01 | 2026-04-13 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |
| `300630.SZ` | 2017-03-28 | 2025-05-22 | `szse_delisted_register.csv` | `EXPLICIT_TERMINATION_FIELD` | True | False | `RAW_OHLCV_NOT_MANIFEST_VERIFIED;HISTORICAL_STATUS_PROVENANCE_INCOMPLETE;HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` |

---

## 5. Candidate Membership-Blocked Symbols (18 SSE Stocks)

All 18 stocks originate from `sse_delisted_register.csv` where the raw date field is `暂停上市日期` (`BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS`). Under the fail-closed provenance rule, `暂停上市日期` cannot be automatically assumed to be `delisting_date` (摘牌日) without source-wide resolution:

| Symbol | Listing Date | Raw Date | Source File | Raw Date Field | Semantic Status | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `600193.SH` | 1999-05-27 | 2026-07-06 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600213.SH` | 1999-08-31 | 2024-10-17 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600225.SH` | 2000-01-27 | 2025-03-06 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600355.SH` | 2002-06-13 | 2026-04-27 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600421.SH` | 2004-06-07 | 2026-06-26 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600462.SH` | 2003-09-03 | 2025-07-21 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600599.SH` | 2001-08-28 | 2026-06-26 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600608.SH` | 1992-03-27 | 2026-07-03 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600636.SH` | 1993-03-16 | 2026-06-29 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600696.SH` | 1993-12-06 | 2026-06-29 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600705.SH` | 1996-05-16 | 2025-05-27 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600804.SH` | 1994-01-03 | 2025-07-03 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `600898.SH` | 1996-04-18 | 2025-02-10 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `601028.SH` | 2011-11-07 | 2025-05-27 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `603003.SH` | 2012-08-17 | 2025-07-03 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `603388.SH` | 2017-03-24 | 2025-12-05 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `603963.SH` | 2017-09-22 | 2025-03-21 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |
| `605081.SH` | 2021-02-09 | 2026-07-03 | `sse_delisted_register.csv` | `暂停上市日期` | `PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE` | `BLOCKED_DELISTING_DATE_SOURCE_SEMANTICS` |

---

## 6. Candidate Execution Readiness

- **Execution Ready Count**: **0**
- **Execution Blocked Count**: **35** (100% of candidates)

### Blocker Taxonomy and Counts

| Blocker Code | Affected Symbol Count | Description |
| :--- | :--- | :--- |
| `PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE` | **18** | SSE delisting semantics unresolved in official register |
| `RAW_OHLCV_NOT_MANIFEST_VERIFIED` | **35** | No mounted Raw CSV or manifest SHA256 match |
| `HISTORICAL_STATUS_PROVENANCE_INCOMPLETE` | **35** | No historical status intervals or valid provenance sidecar records |
| `HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE` | **35** | No historical sector intervals or valid provenance sidecar records |

---

## 7. Sub-component Data Readiness for Candidates

### A. Candidate Raw OHLCV Readiness
- **Verified**: `0`
- **Missing**: `35`
- **Hash Mismatch**: `0`

### B. Candidate Historical Status Provenance
- **Verified Provenance**: `0`
- **Incomplete Provenance**: `35`

### C. Candidate Historical Sector Provenance
- **Verified Provenance**: `0`
- **Incomplete Provenance**: `35`

---

## 8. Full Dataset Preflight Summary

- **`formal_full_market_ready`**: `False`
- **`research_grade_candidate`**: `False`
- **`official_universe_set_match_raw`**: `False`
- **`official_universe_set_match`**: `False`
- **`official_universe_source_semantics_verified`**: `False`
- **`universe_missing_symbol_count`**: `59`
- **`universe_extra_symbol_count`**: `0`
- **`raw_dataset_hash_match`**: `True`
- **`daily_raw_coverage_fresh`**: `True`
- **`daily_raw_bar_coverage`**: `0.401` (40.10% < 98% gate threshold)
- **`corporate_action_dataset_complete`**: `False`
- **`status_dataset_complete`**: `False`
- **`sector_dataset_complete`**: `False`
- **`raw_execution_price_ready`**: `False`

### Remaining Research Blockers
1. `RAW_EXECUTION_PRICE_INSUFFICIENT`: daily coverage 40.10% (3334/5655 stocks flagged data_missing=True)
2. `OFFICIAL_UNIVERSE_SOURCE_DATE_SEMANTICS_UNVERIFIED`: 18 SSE stocks require authoritative delisting resolution
3. `CORPORATE_ACTION_OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED`: Event-set level reconciliation incomplete
4. `STATUS_PROVENANCE_SIDECAR_MISSING`: 0.0% verified status coverage
5. `SECTOR_PROVENANCE_SIDECAR_MISSING`: 0.0% verified sector coverage

---

## 9. Research Suite Hard-Gate Verification

- **Command**: `a-share-agent research-suite --require-research-grade`
- **Exit Code**: `3` (`!= 0`)
- **Blocked**: `True`
- **Output**: `BLOCKED: formal_full_market_ready is false; full-market research grade not ready.`
- **Result**: No unauthorized backtest run or performance simulation was triggered.

---

## 10. Safety and Audit Integrity Rule

> `membership_ready` is reference-data readiness only. `execution_ready` is strictly required before any security can become eligible for strategy trading in backtests. This audit does not alter `security_master.csv`, synthesize OHLCV prices, or manufacture provenance intervals.
