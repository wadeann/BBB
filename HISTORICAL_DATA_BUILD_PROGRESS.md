# Historical Data Build Progress

Generated: 2026-10-07T00:19:53.277741+00:00

## Universe
- Snapshots audited: 5
- Legacy-schema snapshots: 0
- Non-target snapshot rows filtered: 0
- Missing in local: 59
- Extra in local: 0
- Exact set match: False
- Detailed diff: `official_universe_set_diff_detailed.csv`

## Raw OHLCV
- Mounted: True
- Hash match: True
- Actual files: 5657
- Actual rows: 2728892
- Dataset hash: `305be2e1c791a043d29c4de56a93bde0fe16ef41a8a63e02e16e9d68fc66df1e`
- Daily coverage fresh: True

## Corporate Actions
- Official register valid: False
- Official events: 0
- Production events: 91
- Missing: 0
- Extra: 0
- Conflicts: 0
- Dataset complete: False

## Historical Status
- Intervals: 5742
- Verified intervals: 0
- Source coverage: 0.00%
- Dataset complete: False

## Historical Sector
- Intervals: 5676
- Verified intervals: 0
- Source coverage: 0.00%
- Dataset complete: False

## Remaining blockers
- Universe unresolved differences: missing=59, extra=0
- Independent Corporate Action event set incomplete/unverified
- Status provenance coverage=0.00%
- Sector provenance coverage=0.00%

## Safety rule
This audit never makes `formal_full_market_ready` true by itself. Formal readiness must be produced by the normal Preflight after all real datasets and provenance gates pass.
