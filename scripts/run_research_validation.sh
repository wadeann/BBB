#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
AGENT="${AGENT:-$ROOT/.venv/bin/a-share-agent}"
START_DATE="${START_DATE:-2024-10-01}"
END_DATE="${END_DATE:-2026-09-30}"
UNIVERSE_MODE="${UNIVERSE_MODE:-prefer_point_in_time}"
MAX_UNIVERSE="${MAX_UNIVERSE:-0}"
INCLUDE_LLM="${INCLUDE_LLM:-0}"

args_common=(--root "$ROOT" --backend production)

printf '\n[1/4] Intel MCP probe\n'
"$AGENT" "${args_common[@]}" mcp-probe --service intel

printf '\n[2/4] Research data preflight\n'
preflight=("$AGENT" "${args_common[@]}" research-preflight --start "$START_DATE" --end "$END_DATE" --universe-mode "$UNIVERSE_MODE" --sample-size 30)
if [[ "$MAX_UNIVERSE" != "0" ]]; then preflight+=(--max-universe "$MAX_UNIVERSE"); fi
"${preflight[@]}"

printf '\n[3/4] Deterministic research suite\n'
cmd=("$AGENT" "${args_common[@]}" research-suite --start "$START_DATE" --end "$END_DATE" --universe-mode "$UNIVERSE_MODE" \
  --experiment baseline --experiment no_triple_golden_cross --experiment core_signal_focus --experiment router_disabled --experiment sector_disabled)
if [[ "$MAX_UNIVERSE" != "0" ]]; then cmd+=(--max-universe "$MAX_UNIVERSE"); fi
"${cmd[@]}"

if [[ "$INCLUDE_LLM" == "1" ]]; then
  printf '\n[4/4] LLM probe + A/B suite\n'
  "$AGENT" --root "$ROOT" llm-probe
  llm_cmd=("$AGENT" "${args_common[@]}" research-suite --start "$START_DATE" --end "$END_DATE" --universe-mode "$UNIVERSE_MODE" --include-llm \
    --experiment baseline --experiment llm_gate_baseline --experiment no_triple_golden_cross --experiment llm_gate_no_triple)
  if [[ "$MAX_UNIVERSE" != "0" ]]; then llm_cmd+=(--max-universe "$MAX_UNIVERSE"); fi
  "${llm_cmd[@]}"
else
  printf '\n[4/4] LLM A/B skipped (set INCLUDE_LLM=1 to run)\n'
fi

printf '\nLatest feedback package:\n'
"$AGENT" --root "$ROOT" research-latest
