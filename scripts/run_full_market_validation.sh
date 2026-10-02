#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(pwd)}"
PY="${PY:-$ROOT/.venv/bin/a-share-agent}"
START_DATE="${START_DATE:-2026-07-01}"
END_DATE="${END_DATE:-2026-09-30}"
RUN_LLM="${RUN_LLM:-0}"

cd "$ROOT"

echo "[1/4] full-market PIT preflight"
"$PY" --root "$ROOT" --backend production research-preflight \
  --start "$START_DATE" --end "$END_DATE" \
  --universe-mode strict_point_in_time \
  --sample-size 60 --require-research-grade

echo "[2/4] deterministic full-market suite"
"$PY" --root "$ROOT" --backend production research-suite \
  --start "$START_DATE" --end "$END_DATE" \
  --universe-mode strict_point_in_time \
  --experiment baseline \
  --experiment no_triple_golden_cross \
  --experiment core_signal_focus \
  --experiment router_disabled \
  --experiment sector_disabled

if [[ "$RUN_LLM" == "1" ]]; then
  echo "[3/4] LLM probe"
  "$PY" --root "$ROOT" llm-probe
  echo "[4/4] LLM no-triple A/B"
  "$PY" --root "$ROOT" --backend production research-suite \
    --start "$START_DATE" --end "$END_DATE" \
    --universe-mode strict_point_in_time \
    --include-llm \
    --experiment no_triple_golden_cross \
    --experiment llm_gate_no_triple
else
  echo "[3/4] LLM skipped (RUN_LLM=0)"
fi

"$PY" --root "$ROOT" research-latest || true
printf '\nSend back:\n  data/diagnostics/latest_research_preflight.json\n  latest data/research/runs/<suite>/feedback_bundle.zip\n'
