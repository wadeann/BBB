#!/usr/bin/env python3
"""Build official_corporate_actions_register.csv by querying the intel MCP tool for each symbol."""
import csv, json, time, hashlib, sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "data" / "backtest"
MASTER = BACKTEST / "security_master.csv"
OUT = BACKTEST / "official_corporate_actions_register.csv"
MANIFEST = BACKTEST / "official_corporate_actions_manifest.json"
MCP_URL = "http://localhost:9001/mcp"

def mcp_call(symbol):
    payload = json.dumps({"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"corporate_action_history","arguments":{"symbol":symbol}}}).encode()
    req = Request(MCP_URL, data=payload, headers={"Content-Type":"application/json"})
    resp = json.loads(urlopen(req, timeout=30).read())
    text = resp.get("result",{}).get("content",[{}])[0].get("text","{}")
    return json.loads(text)

def main():
    if not MASTER.exists():
        print(f"ERROR: {MASTER} not found")
        return 1

    # Read all symbols
    symbols = []
    with open(MASTER, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol","").strip()
            if sym:
                symbols.append(sym)
    print(f"Total symbols in master: {len(symbols)}")

    # If register already exists, check how many symbols we have
    existing_symbols = set()
    if OUT.exists():
        with open(OUT, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                existing_symbols.add(row.get("symbol",""))
        print(f"Existing register has {len(existing_symbols)} symbols")

    all_events = []
    errors = 0
    for i, sym in enumerate(symbols):
        if sym in existing_symbols:
            continue
        try:
            result = mcp_call(sym)
            if result.get("status") == "success" and result.get("events"):
                for ev in result["events"]:
                    all_events.append({
                        "symbol": sym,
                        "action_type": ev.get("action_type",""),
                        "ex_date": ev.get("ex_date",""),
                        "record_date": ev.get("record_date",""),
                        "description": ev.get("description",""),
                        "status": "MCP_10jqka",
                    })
            if (i+1) % 100 == 0:
                print(f"  Progress: {i+1}/{len(symbols)}, events so far: {len(all_events)}")
            time.sleep(0.1)  # rate limit
        except Exception as e:
            errors += 1
            if errors > 10:
                print(f"Too many errors, stopping. Last: {e}")
                break

    print(f"Total new events collected: {len(all_events)}")

    # Merge with existing
    fieldnames = ["symbol","action_type","ex_date","record_date","description","status"]
    rows = []
    if OUT.exists():
        with open(OUT, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    rows.extend(all_events)

    # Deduplicate by key
    seen = set()
    deduped = []
    for r in rows:
        key = (r["symbol"], r["action_type"], r["ex_date"], r["record_date"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(deduped)

    print(f"Wrote {len(deduped)} events to {OUT}")

    # Generate lightweight manifest for audit validation
    register_sha = hashlib.sha256(open(OUT,"rb").read()).hexdigest()
    manifest = {
        "source_type": "MCP_10jqka",
        "source_dataset_id": "intel_mcp_corporate_action_history",
        "register_sha256": register_sha,
        "event_count": len(deduped),
        "source_files": []
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote manifest to {MANIFEST}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
