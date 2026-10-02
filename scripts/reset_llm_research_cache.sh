#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
CACHE="$ROOT/data/backtest/llm_cache"
mkdir -p "$CACHE"
find "$CACHE" -maxdepth 1 -type f -name '*.json' -print -delete
printf 'LLM research cache cleared: %s\n' "$CACHE"
