#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LATEST="$ROOT/data/research/runs/latest.json"
if [[ ! -f "$LATEST" ]]; then
  echo "No research suite found. Run research-suite first." >&2
  exit 2
fi
cat "$LATEST"
