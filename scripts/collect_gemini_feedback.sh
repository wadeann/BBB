#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(pwd)}"
cd "$ROOT"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$ROOT/data/research/handoff_$STAMP"
mkdir -p "$OUT_DIR"

copy_if_exists() {
  local src="$1"
  local dst="${2:-$(basename "$1")}" 
  if [[ -f "$src" ]]; then
    cp "$src" "$OUT_DIR/$dst"
  fi
}

copy_if_exists "$ROOT/data/diagnostics/latest_research_preflight.json"
copy_if_exists "$ROOT/data/diagnostics/latest_mcp_probe.json"
copy_if_exists "$ROOT/data/diagnostics/latest_llm_probe.json"
copy_if_exists "$ROOT/VALIDATION_REPORT_COMPLETED.md"
copy_if_exists "$ROOT/CHANGELOG_V0.7.md"
copy_if_exists "$ROOT/GEMINI_HANDOFF_V07.md"

LATEST_JSON="$ROOT/data/research/runs/latest.json"
if [[ -f "$LATEST_JSON" ]]; then
  cp "$LATEST_JSON" "$OUT_DIR/research_latest.json"
  SUITE_DIR="$(python3 - "$LATEST_JSON" <<'PY'
import json, sys
p=sys.argv[1]
obj=json.load(open(p, encoding='utf-8'))
for k in ('report_dir','suite_dir','path'):
    v=obj.get(k)
    if isinstance(v,str) and v:
        print(v); break
else:
    sid=obj.get('suite_id') or obj.get('id')
    if sid:
        print('data/research/runs/'+sid)
PY
)"
  if [[ -n "$SUITE_DIR" && "$SUITE_DIR" != /* ]]; then
    SUITE_DIR="$ROOT/$SUITE_DIR"
  fi
  if [[ -d "$SUITE_DIR" ]]; then
    mkdir -p "$OUT_DIR/research_suite"
    for f in research_summary.json experiment_metrics.csv FEEDBACK_README.md feedback_bundle.zip; do
      [[ -f "$SUITE_DIR/$f" ]] && cp "$SUITE_DIR/$f" "$OUT_DIR/research_suite/$f"
    done
  fi
fi

# Useful logs only; never copy .env.
mkdir -p "$OUT_DIR/logs"
for f in mcp-probe.log llm-probe.log error.log; do
  [[ -f "$ROOT/data/logs/$f" ]] && tail -n 500 "$ROOT/data/logs/$f" > "$OUT_DIR/logs/$f"
done

ZIP="$ROOT/data/research/gemini_feedback_$STAMP.zip"
python3 - "$OUT_DIR" "$ZIP" <<'PY'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for fn in files:
            p=os.path.join(root,fn)
            z.write(p, os.path.relpath(p, src))
print(out)
PY

echo "Gemini feedback package: $ZIP"
echo "Also upload data/diagnostics/latest_research_preflight.json separately when convenient."
