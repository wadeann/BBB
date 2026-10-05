#!/usr/bin/env python3
"""Daily stock-pick runner: runs stock-pick command and saves results.

Usage:
  python3 daily_pick.py                    # run stock-pick, save to data/daily/
  python3 daily_pick.py --json             # JSON output
  python3 daily_pick.py --symbol 600519.SH # specific symbols
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description="Daily A-share stock pick runner")
    p.add_argument("--symbol", action="append", help="explicit symbols")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--limit", type=int, default=30, help="max candidates")
    p.add_argument("--save", action="store_true", default=True, help="save to file")
    p.add_argument("--print", action="store_true", default=False, help="print to stdout")
    args = p.parse_args()

    cmd = [sys.executable, "-m", "a_share_agent.cli", "stock-pick",
           "--limit", str(args.limit)]
    if args.json:
        cmd.append("--json")
    if args.symbol:
        for sym in args.symbol:
            cmd.extend(["--symbol", sym])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"ERROR: stock-pick failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    output = result.stdout

    if args.print:
        print(output)

    if args.save:
        today = datetime.now().strftime("%Y-%m-%d")
        save_dir = PROJECT_ROOT / "data" / "daily"
        save_dir.mkdir(parents=True, exist_ok=True)
        ext = "json" if args.json else "txt"
        path = save_dir / f"stock_pick_{today}.{ext}"
        path.write_text(output)
        print(f"Saved to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
