#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(pwd)}"
PY="${PY:-$ROOT/.venv/bin/a-share-agent}"
START_DATE="${START_DATE:-2026-07-01}"
END_DATE="${END_DATE:-2026-09-30}"
UNIVERSE_MODE="${UNIVERSE_MODE:-prefer_point_in_time}"
SAMPLE_SIZE="${SAMPLE_SIZE:-30}"
RUN_LLM="${RUN_LLM:-1}"

cd "$ROOT"

echo "[1/5] Version"
"$ROOT/.venv/bin/python" -c 'import a_share_agent; print(a_share_agent.__version__)'

echo "[2/5] Reset old LLM research cache"
bash scripts/reset_llm_research_cache.sh

echo "[3/5] Research preflight: $START_DATE -> $END_DATE"
"$PY" --root "$ROOT" --backend production research-preflight \
  --start "$START_DATE" --end "$END_DATE" \
  --universe-mode "$UNIVERSE_MODE" --sample-size "$SAMPLE_SIZE"

echo "[4/5] Deterministic A/B"
"$PY" --root "$ROOT" --backend production research-suite \
  --start "$START_DATE" --end "$END_DATE" \
  --universe-mode "$UNIVERSE_MODE" \
  --experiment baseline \
  --experiment no_triple_golden_cross

if [[ "$RUN_LLM" == "1" ]]; then
  echo "[5/5] LLM A/B: no_triple vs llm_gate_no_triple"
  "$PY" --root "$ROOT" llm-probe
  "$PY" --root "$ROOT" --backend production research-suite \
    --start "$START_DATE" --end "$END_DATE" \
    --universe-mode "$UNIVERSE_MODE" \
    --include-llm \
    --experiment no_triple_golden_cross \
    --experiment llm_gate_no_triple
else
  echo "[5/5] LLM A/B skipped (RUN_LLM=$RUN_LLM)"
fi

echo
"$PY" --root "$ROOT" research-latest || true

echo "Preflight: $ROOT/data/diagnostics/latest_research_preflight.json"
echo "See latest Research Suite path above; send its feedback_bundle.zip with preflight JSON."
