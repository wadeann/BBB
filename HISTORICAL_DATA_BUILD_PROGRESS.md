# Historical Data Build Progress

Generated: 2026-10-07T07:40:02.432449+00:00

## Universe
- Snapshots audited: 5
- Legacy-schema snapshots: 0
- Non-target snapshot rows filtered: 0
- Missing in local: 0
- Extra in local: 0
- Exact set match: True
- Detailed diff: `official_universe_set_diff_detailed.csv`

## Raw OHLCV
- Mounted: True
- Hash match: True
- Actual files: 5658
- Actual rows: 2729792
- Dataset hash: `c1999d93667b34b6d2ec96b835a0df092baecb9ab9afd5a07a3508973df17652`
- Daily coverage fresh: True

## Corporate Actions
- Official register valid: True
- Official events: 91
- Production events: 91
- Missing: 0
- Extra: 0
- Conflicts: 0
- Dataset complete: True

## Historical Status
- Intervals: 5742
- Verified intervals: 5742
- Source coverage: 100.00%
- Dataset complete: True

## Historical Sector
- Intervals: 5676
- Verified intervals: 5676
- Source coverage: 100.00%
- Dataset complete: True

## Remaining blockers
- None from provenance audit (Preflight still determines formal readiness).

## Safety rule
This audit never makes `formal_full_market_ready` true by itself. Formal readiness must be produced by the normal Preflight after all real datasets and provenance gates pass.
