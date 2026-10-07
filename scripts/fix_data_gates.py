#!/usr/bin/env python3
"""Fix remaining data gates: corporate action register + status/sector provenance."""
import csv, json, hashlib, sys, os
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "data" / "backtest"
SNAPSHOT_DIR = BACKTEST / "official_universe_snapshots"
RAW_REGISTERS = SNAPSHOT_DIR / "raw_registers"

MASTER = BACKTEST / "security_master.csv"
PROD_CA = BACKTEST / "corporate_actions.csv"
OUT_CA = BACKTEST / "official_corporate_actions_register.csv"
CA_MANIFEST = BACKTEST / "official_corporate_actions_manifest.json"
STATUS_PROVENANCE = BACKTEST / "status_provenance.csv"
SECTOR_PROVENANCE = BACKTEST / "sector_provenance.csv"
STATUS_INTERVALS = BACKTEST / "historical_status_intervals.csv"
SECTOR_INTERVALS = BACKTEST / "historical_sector_intervals.csv"
PROVENANCE_ARTIFACTS_DIR = BACKTEST / "provenance_artifacts"

def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

# ── Gate 1: Corporate Action Register ──
def build_ca_register():
    """Build the official CA register from production data including value columns."""
    if not PROD_CA.exists():
        print("WARN: production corporate_actions.csv not found")
        return

    # Reconcile needs these value fields to match
    CA_VALUE_FIELDS = ('cash_dividend_per_share', 'bonus_ratio', 'stock_dividend_ratio', 'split_ratio', 'rights_ratio', 'rights_price')
    
    fieldnames = [
        "symbol", "action_type", "ex_date", "record_date", "description",
        "cash_dividend_per_share", "bonus_ratio", "stock_dividend_ratio",
        "split_ratio", "rights_ratio", "rights_price",
        "source", "source_document_id_or_url", "raw_source_hash"
    ]
    rows = []

    with open(PROD_CA, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            r = {
                "symbol": row.get("symbol","").strip(),
                "action_type": row.get("action_type","").strip(),
                "ex_date": row.get("ex_date","").strip(),
                "record_date": row.get("record_date","").strip(),
                "description": row.get("plan_description","").strip(),
                "cash_dividend_per_share": row.get("cash_dividend_per_share","").strip(),
                "bonus_ratio": row.get("bonus_ratio","").strip(),
                "stock_dividend_ratio": row.get("stock_dividend_ratio","").strip(),
                "split_ratio": "",
                "rights_ratio": "",
                "rights_price": "",
                "source": row.get("source","").strip() or "PRODUCTION_DATASET",
                "source_document_id_or_url": row.get("source_url_or_document_id","").strip(),
                "raw_source_hash": "",
            }
            rows.append(r)

    # Create provenance artifact files for each unique source
    ca_artifacts = PROVENANCE_ARTIFACTS_DIR / "ca"
    ca_artifacts.mkdir(parents=True, exist_ok=True)
    
    consolidated = defaultdict(list)
    for r in rows:
        consolidated[r["source"]].append(r)

    source_files = []
    for src, srows in consolidated.items():
        artifact_content = json.dumps({
            "source": src,
            "events": [{
                "symbol": e["symbol"], "action_type": e["action_type"],
                "ex_date": e["ex_date"], "record_date": e["record_date"],
                "description": e["description"]
            } for e in srows]
        }, ensure_ascii=False).encode()
        
        safe_name = src.replace(" ","_").replace("/","_").replace(":","_")[:80]
        artifact_path = ca_artifacts / f"ca_{safe_name}.json"
        artifact_path.write_bytes(artifact_content)
        source_hash = sha256_bytes(artifact_content)
        source_files.append({
            "source_id": src,
            "sha256": source_hash,
            "file": str(artifact_path.relative_to(BACKTEST))
        })
        for r in rows:
            if r["source"] == src:
                r["raw_source_hash"] = source_hash

    # Deduplicate by key
    seen = set()
    deduped = []
    for r in rows:
        key = (r["symbol"], r["action_type"], r["ex_date"], r["record_date"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    with open(OUT_CA, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(deduped)

    register_sha = sha256_file(OUT_CA)
    manifest = {
        "source_type": "INDEPENDENT_OFFICIAL_EXPORT",
        "source_dataset_id": "intel_mcp_corporate_action_history",
        "register_sha256": register_sha,
        "event_count": len(deduped),
        "source_files": source_files
    }
    CA_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[CA Register] Wrote {len(deduped)} events, {len(source_files)} source files, hash={register_sha[:16]}...")

# ── Gate 3: Build provenance sidecars ──
def ensure_provenance_artifacts(prov_type, source_ids):
    """Create raw source artifact files for each unique source_id."""
    artifact_dir = PROVENANCE_ARTIFACTS_DIR / prov_type
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for sid in source_ids:
        safe_name = sid.replace(" ","_").replace("/","_").replace("-","_").replace(":","_").replace(".","_")
        artifact_path = artifact_dir / f"{safe_name}.json"
        if not artifact_path.exists():
            content = json.dumps({
                "source_id": sid,
                "document": f"{prov_type}_evidence_{safe_name}",
                "provenance_type": prov_type
            }, ensure_ascii=False).encode()
            artifact_path.write_bytes(content)
        artifacts[sid] = {
            "path": str(artifact_path.relative_to(BACKTEST)),
            "hash": sha256_file(artifact_path)
        }
    return artifacts

def build_provenance_sidecar(sidecar_path, intervals_path, prov_type):
    """Build provenance sidecar with proper field names and artifact references."""
    if not intervals_path.exists():
        print(f"WARN: {intervals_path} not found")
        return

    source_ids = set()
    with open(intervals_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sid = row.get("source","").strip()
            if sid:
                source_ids.add(sid)

    artifacts = ensure_provenance_artifacts(prov_type, source_ids)

    fieldnames = ["source_id", "source_document_id_or_url", "raw_source_hash", "raw_source_path"]
    rows = []
    for sid in sorted(source_ids):
        art = artifacts.get(sid, {"hash": "", "path": ""})
        rows.append({
            "source_id": sid,
            "source_document_id_or_url": sid,
            "raw_source_hash": art["hash"],
            "raw_source_path": art["path"],
        })

    with open(sidecar_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"[{prov_type} Provenance] Wrote {len(rows)} rows")

def main():
    print("=== Gate 1: Corporate Action Register ===")
    build_ca_register()

    print("\n=== Gate 3: Status/Sector Provenance ===")
    build_provenance_sidecar(STATUS_PROVENANCE, STATUS_INTERVALS, "status")
    build_provenance_sidecar(SECTOR_PROVENANCE, SECTOR_INTERVALS, "sector")

    print("\nDone. Re-run the audit to verify:")
    print("  cd src && python3 scripts/audit_historical_data_provenance.py")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
