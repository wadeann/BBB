# v0.7.6 — Historical Data Provenance Hardening

This release does not change strategy logic, scoring, sizing, stop thresholds, Router behavior, or LLM prompts.

## Changes

- Added `a_share_agent.backtest.provenance_audit` for strict, reusable provenance checks.
- Status and sector interval coverage now requires explicit sidecar provenance (`source_id`, concrete document/URL, raw-source SHA256). Generic source labels do not count as verified provenance.
- Independent Corporate Action registers now require:
  - manifest-bound register SHA256,
  - independent source dataset id,
  - manifest event count,
  - enumerated raw source files with SHA256,
  - row-level document id/URL and raw-source hash.
- Added `scripts/audit_historical_data_provenance.py` to produce:
  - `official_universe_set_diff_detailed.csv`,
  - `status_provenance_audit.csv`,
  - `sector_provenance_audit.csv`,
  - `corporate_action_set_diff.csv`,
  - `historical_data_provenance_audit.json`,
  - `HISTORICAL_DATA_BUILD_PROGRESS.md`.
- Universe differences are classified into root-cause buckets instead of being silently fixed.
- Full-market Preflight remains fail-closed until all authentic datasets and provenance gates pass.

## Non-goals

v0.7.6 does not download, synthesize, interpolate, or infer missing historical market data. Missing real data remains an explicit blocker.
