#!/bin/bash
# Daily A-Share stock selection runner
# Cron: 0 18 * * 1-5 /home/wade/workspace/ai/codexA/src/scripts/run_daily.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

# Source .env for MCP credentials
if [ -f .env ]; then
    set -a
    . .env
    set +a
fi

# Ensure output directory exists
mkdir -p data/daily

DATE_TAG=$(date +%Y%m%d)

echo "=== Daily stock pick: $(date +%Y-%m-%d) ==="

# Save JSON version
python3.11 -m a_share_agent.cli stock-pick --json --limit 30 > data/daily/stock_pick_${DATE_TAG}.json 2>&1
echo "JSON: data/daily/stock_pick_${DATE_TAG}.json"

# Save readable text version
python3.11 -m a_share_agent.cli stock-pick --limit 30 > data/daily/stock_pick_${DATE_TAG}.txt 2>&1
echo "TXT:  data/daily/stock_pick_${DATE_TAG}.txt"

echo "=== Done: $(date +%Y-%m-%d %H:%M) ==="
