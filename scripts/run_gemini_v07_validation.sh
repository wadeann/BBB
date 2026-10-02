#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(pwd)}"
AGENT="${AGENT:-$ROOT/.venv/bin/a-share-agent}"
PHASE="${PHASE:-preflight}"
START_3M="${START_3M:-2026-07-01}"
END_3M="${END_3M:-2026-09-30}"
START_2Y="${START_2Y:-2024-10-01}"
END_2Y="${END_2Y:-2026-09-30}"

cd "$ROOT"

case "$PHASE" in
  test)
    echo "[v0.7] code regression"
    PYTHONPATH=. "$ROOT/.venv/bin/pytest" -q
    ;;
  mcp)
    echo "[v0.7] intel MCP probe"
    "$AGENT" --root "$ROOT" --backend production mcp-probe --service intel
    ;;
  preflight)
    echo "[v0.7] strict PIT research preflight"
    "$AGENT" --root "$ROOT" --backend production research-preflight \
      --start "$START_3M" --end "$END_3M" \
      --universe-mode strict_point_in_time \
      --sample-size 60 --require-research-grade
    ;;
  smoke)
    echo "[v0.7] 50-symbol interface smoke; DO NOT interpret profit"
    "$AGENT" --root "$ROOT" --backend production research-suite \
      --start "$START_3M" --end "$END_3M" \
      --universe-mode strict_point_in_time --max-universe 50 \
      --experiment baseline --experiment no_triple_golden_cross
    ;;
  perf)
    echo "[v0.7] 500-symbol performance run; DO NOT treat as final full-market evidence"
    "$AGENT" --root "$ROOT" --backend production research-suite \
      --start "$START_3M" --end "$END_3M" \
      --universe-mode strict_point_in_time --max-universe 500 \
      --experiment baseline --experiment no_triple_golden_cross
    ;;
  full3m)
    echo "[v0.7] 3-month FULL MARKET deterministic research"
    "$AGENT" --root "$ROOT" --backend production research-suite \
      --start "$START_3M" --end "$END_3M" \
      --universe-mode strict_point_in_time \
      --experiment baseline \
      --experiment no_triple_golden_cross \
      --experiment core_signal_focus \
      --experiment router_disabled \
      --experiment sector_disabled
    ;;
  llm3m)
    echo "[v0.7] LLM probe"
    "$AGENT" --root "$ROOT" llm-probe
    echo "[v0.7] 3-month FULL MARKET LLM A/B"
    "$AGENT" --root "$ROOT" --backend production research-suite \
      --start "$START_3M" --end "$END_3M" \
      --universe-mode strict_point_in_time --include-llm \
      --experiment no_triple_golden_cross \
      --experiment llm_gate_no_triple
    ;;
  full2y)
    echo "[v0.7] 2-year FULL MARKET deterministic research"
    echo "Only run after 3-month preflight/full-market run is RESEARCH_GRADE."
    "$AGENT" --root "$ROOT" --backend production research-suite \
      --start "$START_2Y" --end "$END_2Y" \
      --universe-mode strict_point_in_time \
      --experiment baseline \
      --experiment no_triple_golden_cross \
      --experiment core_signal_focus \
      --experiment router_disabled \
      --experiment sector_disabled
    ;;
  collect)
    bash "$ROOT/scripts/collect_gemini_feedback.sh"
    ;;
  *)
    cat >&2 <<USAGE
Unknown PHASE=$PHASE
Use one of:
  test | mcp | preflight | smoke | perf | full3m | llm3m | full2y | collect
Example:
  PHASE=preflight bash scripts/run_gemini_v07_validation.sh
USAGE
    exit 2
    ;;
esac
