# Missing History Readiness Report

**Generated At**: 2026-10-02T22:58:00+08:00  
**Audit Scope**: Latest Master Research Readiness Audit (Criteria 1–29, Code Version Binding, and Missing History Readiness)

---

## 1. Git State

- **Branch**: `master`
- **HEAD Commit**: `2c9b99e81be139b84c8919828f3ebc3213fc660c` (`Merge pull request #18 from wadeann/fix/v081-raw-ohlcv-provenance`)
- **Recent Commits**:
  - `2c9b99e` Merge pull request #18 from wadeann/fix/v081-raw-ohlcv-provenance
  - `0f41c62` feat(data): add per-file Raw OHLCV provenance audit
  - `977bf5a` Merge PR #17: independent research provenance gates
  - `6e260e6` test: prove evidence-backed source gates have reachable PASS paths
  - `c7a34f5` fix(data): preserve runtime rule diagnostic while gating research on provenance
  - `71f190c` test: stop treating local semantics flags as independent evidence
  - `1ed3d03` fix(data): separate sample validity from evidence binding
- **Working Tree**: Auditing & readiness version binding code only (zero modifications to strategy, signal, router, scoring, position, stop loss, or LLM prompts).

---

## 2. Test Execution

- **Command**: `pytest -q`
- **Result**:
  - `passed`: 100
  - `failed`: 0
  - `xfailed`: 1 (`test_strict_research_gate_blocks_incomplete_production_actions`)
  - `warnings`: 1 (StarletteDeprecationWarning regarding httpx testclient)
  - `runtime`: 11.92s
- **Status**: 100% Passed / 0 Errors

---

## 3. Code Version Binding & Artifact Currency

All formal readiness artifacts now bind to their producing Git commit:

| Artifact File | Producer Git Commit | Producer Version | Stale |
| :--- | :--- | :--- | :--- |
| `latest_research_preflight.json` | `2c9b99e81be139b84c8919828f3ebc3213fc660c` | `0.7.7` | `False` |
| `historical_data_provenance_audit.json` | `2c9b99e81be139b84c8919828f3ebc3213fc660c` | `0.7.7` | `False` |
| `security_master_missing_history_readiness_summary.json` | `2c9b99e81be139b84c8919828f3ebc3213fc660c` | `0.7.7` | `False` |
| `daily_raw_coverage_manifest.json` | `2c9b99e81be139b84c8919828f3ebc3213fc660c` | `0.7.7` | `False` |
| `raw_dataset_manifest.json` | `2c9b99e81be139b84c8919828f3ebc3213fc660c` | `0.7.7` | `False` |

- **`preflight_artifact_current`**: `True` (`git_commit_sha == 当前运行代码 HEAD`)
- **`artifact_stale`**: `False`

---

## 4. Criteria 1–29 Checklist Evaluation

| # | Criterion Name | Status | Reason / Current Gate Value |
| :--- | :--- | :--- | :--- |
| 1 | `1_market_universe_coverage` | `FAIL` | Min daily active symbols = 0 (trusted trading calendar not yet verified) |
| 2 | `2_exchange_coverage` | `PASS` | All 5 boards (SSE_MAIN: 1734, STAR: 619, SZSE_MAIN: 1544, CHINEXT: 1410, BSE: 348) present |
| 3 | `3_historical_delisted_preserved` | `FAIL` | Independent delisted register reconciliation incomplete |
| 4 | `4_no_prelisting_leakage` | `PASS` | Zero pre-listing leakage observed |
| 5 | `5_no_post_delisting_leakage` | `PASS` | Zero post-delisting leakage observed |
| 6 | `6_status_dataset_complete` | `FAIL` | Status PIT intervals missing verifiable provenance sidecar (0.00% verified) |
| 7 | `7_sector_dataset_complete` | `FAIL` | Sector PIT intervals missing verifiable provenance sidecar (0.00% verified) |
| 8 | `8_corporate_action_dataset_complete` | `FAIL` | Official CA register missing; production actions (91) unverified against official sources |
| 9 | `9_raw_execution_price_ready` | `FAIL` | Min daily active Raw coverage is 40.07% < 98% gate |
| 10 | `10_daily_raw_bar_coverage` | `FAIL` | Daily active Raw coverage below 98% threshold (485/485 days below 98%) |
| 11 | `11_each_exchange_raw_coverage` | `FAIL` | BSE coverage = 0.0%, CHINEXT = 21.45%, SSE_MAIN = 45.52% < 98% |
| 12 | `12_official_universe_set_match` | `FAIL` | 59 missing snapshot observations, 18 SSE symbols date semantics unverified |
| 13 | `13_historical_trading_rules_verified` | `FAIL` | Runtime checks pass (18/18), but independent official document provenance is missing |
| 14 | `14_benchmark_coverage` | `FAIL` | Benchmark missing exact match against trusted official trading calendar |
| 15 | `15_survivorship_bias` | `FAIL` | Survivorship bias protection requires trusted universe match + delisted completeness |
| 16 | `16_raw_dataset_hash_match` | `PASS` | Raw OHLCV dataset cryptographic hash matches manifest (`ab5624c...`, 2323 files, 1187093 rows) |
| 17 | `17_daily_raw_coverage_fresh` | `PASS` | Coverage CSV hash (`dc4d224...`) matches coverage manifest |
| 18 | `18_status_provenance_sidecar` | `FAIL` | `status_provenance.csv` sidecar missing from `data/backtest/` |
| 19 | `19_sector_provenance_sidecar` | `FAIL` | `sector_provenance.csv` sidecar missing from `data/backtest/` |
| 20 | `20_official_universe_source_semantics_verified` | `FAIL` | SSE delisted register field `暂停上市日期` lacks source-wide official delisting resolution |
| 21 | `21_status_full_window_pit_coverage` | `FAIL` | Full research window PIT coverage incomplete without verified provenance |
| 22 | `22_sector_full_window_pit_coverage` | `FAIL` | Full research window PIT coverage incomplete without verified provenance |
| 23 | `23_security_master_interval_integrity` | `PASS` | Listing and delisting dates maintain valid boundary intervals |
| 24 | `24_delisted_register_reconciliation_complete` | `FAIL` | Historical delisted symbols not reconciled to authoritative exchange registers |
| 25 | `25_benchmark_exact_calendar_and_warmup` | `FAIL` | Benchmark calendar exact match blocked pending trusted calendar verification |
| 26 | `26_trusted_trading_calendar_verified` | `FAIL` | `trusted_trading_calendar.csv` and manifest missing from `data/backtest/` |
| 27 | `27_daily_raw_coverage_exact_calendar` | `FAIL` | Daily coverage dates cannot be certified without trusted official calendar |
| 28 | `28_official_snapshot_and_register_hashes_verified` | `PASS` | Snapshot CSVs and raw registers match manifest SHA256 fingerprints |
| 29 | `29_trading_rule_provenance_verified` | `FAIL` | `data/backtest/trading_rules_provenance.json` missing physical rule documents |

- **Criteria Pass Count**: 5 / 29
- **Criteria Fail Count**: 24 / 29
- **`formal_full_market_ready`**: `False`
- **`research_grade_candidate`**: `False`

---

## 5. Trusted Trading Calendar Full-Window Integrity Contract

- Manifest requirements enforced:
  - `coverage_scope == "FULL_EXCHANGE_CALENDAR"`
  - `coverage_start <= research_start`
  - `coverage_end >= research_end`
  - Source artifacts must declare and substantiate `coverage_start <= research_start` and `coverage_end >= research_end`.
- Data span requirements enforced:
  - Earliest calendar date must cover the beginning of research window (`2024-10-01`).
  - Latest calendar date must cover the end of research window (`2026-09-30`).
- Adversarial test verified in pytest:
  - An authentic official calendar artifact covering only `2026-07-01` to `2026-09-30` fails closed (`verified=False`, `coverage_contract_valid=False`, `calendar_range_complete=False`).

---

## 6. Candidate Membership Readiness vs. Execution Readiness (35 Candidates)

- **Candidate Count**: 35
- **Membership Ready Count**: 17 (SZSE stocks with explicit `终止上市日期`)
- **Membership Blocked Count**: 18 (SSE stocks with unverified `暂停上市日期`)
- **Execution Ready Count**: **0** (0%)
- **Execution Blocked Count**: **35** (100%)

### Blocker Breakdown:
1. `RAW_OHLCV_NOT_MANIFEST_VERIFIED`: 35 / 35
2. `HISTORICAL_STATUS_PROVENANCE_INCOMPLETE`: 35 / 35
3. `HISTORICAL_SECTOR_PROVENANCE_INCOMPLETE`: 35 / 35
4. `PIT_MEMBERSHIP_PROVENANCE_INCOMPLETE`: 18 / 35

> **Safety Rule**: `membership_ready` is reference-data readiness only. `execution_ready` is strictly required before any security can become eligible for strategy trading in backtests. No candidate is written to `security_master.csv`.

---

## 7. Current Hard Blockers Summary

1. **P0-A Trusted Official Trading Calendar**: `trusted_trading_calendar.csv` and manifest missing.
2. **P0-B Universe Source Semantics**: 18 SSE stocks require source-wide official delisting resolution.
3. **P0-C Raw OHLCV Coverage**: Daily active coverage at 40.07% (below 98% requirement; 3,334 / 5,655 stocks missing).
4. **P0-D Status Provenance**: 0.00% verified provenance sidecar coverage.
5. **P0-E Sector Provenance**: 0.00% verified provenance sidecar coverage.
6. **P0-F Corporate Actions**: Official register and manifest missing; 91 production events unverified.
7. **P0-G Trading Rule Provenance**: Runtime checks pass (18/18), but physical source document provenance sidecar missing.
