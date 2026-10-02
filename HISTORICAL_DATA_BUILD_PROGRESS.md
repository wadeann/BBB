# Historical Data Build Progress

Generated: 2026-10-02T11:11:37.406701+00:00

## Universe
- Snapshots audited: 5
- Missing in local: 77
- Extra in local: 0
- Exact set match: False
- Detailed diff: `official_universe_set_diff_detailed.csv`

## Raw OHLCV
- Mounted: True
- Hash match: True
- Actual files: 2323
- Actual rows: 1187093
- Dataset hash: `ab5624c66081de18c37c425e195c9b44db814f4e37450bd73d97b5787341d93b`
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
- Universe unresolved differences: missing=77, extra=0
- Independent Corporate Action event set incomplete/unverified
- Status provenance coverage=0.00%
- Sector provenance coverage=0.00%

## Safety rule
This audit never makes `formal_full_market_ready` true by itself. Formal readiness must be produced by the normal Preflight after all real datasets and provenance gates pass.
