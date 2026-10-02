#!/usr/bin/env python3
"""Reconcile production corporate actions against an INDEPENDENT official event register.

This script intentionally never builds the official register from production data.
Required external files:
  data/backtest/official_corporate_actions_register.csv
  data/backtest/official_corporate_actions_manifest.json
"""
from __future__ import annotations
import csv, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent; BACKTEST_DIR=ROOT/"data"/"backtest"; PROD_CA_FILE=BACKTEST_DIR/"corporate_actions.csv"; OFFICIAL_CA_FILE=BACKTEST_DIR/"official_corporate_actions_register.csv"; OFFICIAL_CA_MANIFEST=BACKTEST_DIR/"official_corporate_actions_manifest.json"; DIFF_FILE=ROOT/"corporate_action_set_diff.csv"

def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def official_register_status()->dict:
    if not OFFICIAL_CA_FILE.exists() or not OFFICIAL_CA_MANIFEST.exists(): return {"valid":False,"reason":"INDEPENDENT_OFFICIAL_CA_REGISTER_OR_MANIFEST_MISSING"}
    try: manifest=json.loads(OFFICIAL_CA_MANIFEST.read_text(encoding="utf-8"))
    except Exception: return {"valid":False,"reason":"OFFICIAL_CA_MANIFEST_INVALID_JSON"}
    source_type=str(manifest.get("source_type") or ""); expected_hash=str(manifest.get("register_sha256") or ""); actual_hash=sha256_file(OFFICIAL_CA_FILE)
    valid=bool(source_type=="INDEPENDENT_OFFICIAL_EXPORT" and expected_hash and expected_hash==actual_hash and bool(manifest.get("source_dataset_id")))
    return {"valid":valid,"reason":"OK" if valid else "OFFICIAL_CA_PROVENANCE_OR_HASH_INVALID","source_type":source_type,"source_dataset_id":manifest.get("source_dataset_id"),"expected_hash":expected_hash or None,"actual_hash":actual_hash}

def _event_key(r:dict)->tuple[str,str,str,str]: return (str(r.get("symbol") or ""),str(r.get("action_type") or ""),str(r.get("ex_date") or ""),str(r.get("record_date") or ""))

def reconcile_corporate_actions()->dict:
    status=official_register_status()
    if not status["valid"]:
        with DIFF_FILE.open("w",encoding="utf-8-sig",newline="") as f:
            fields=["status","symbol","action_type","ex_date","record_date","official_cash_div","loaded_cash_div","official_bonus","loaded_bonus","detail"]; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerow({"status":"OFFICIAL_REGISTER_UNAVAILABLE","detail":status["reason"]})
        metrics={"official_register_valid":False,"expected_events":0,"loaded_events":0,"matched_events":0,"missing_events":0,"extra_events":0,"conflicting_events":0,"dataset_complete":False,"reason":status["reason"]}; print(json.dumps(metrics,indent=2,ensure_ascii=False)); return metrics
    official_map={}
    with OFFICIAL_CA_FILE.open("r",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k=_event_key(r)
            if all(k): official_map[k]=r
    loaded_map={}
    if PROD_CA_FILE.exists():
        with PROD_CA_FILE.open("r",encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                k=_event_key(r)
                if all(k): loaded_map[k]=r
    expected_set,loaded_set=set(official_map),set(loaded_map); missing,extra,intersection=expected_set-loaded_set,loaded_set-expected_set,expected_set&loaded_set; conflicts=[]
    for k in intersection:
        off,lod=official_map[k],loaded_map[k]
        for field in ("cash_dividend_per_share","bonus_ratio","stock_dividend_ratio","split_ratio","rights_ratio","rights_price"):
            try: a,b=float(off.get(field) or 0),float(lod.get(field) or 0)
            except Exception: a,b=str(off.get(field) or ""),str(lod.get(field) or "")
            if a!=b: conflicts.append((k,off,lod)); break
    rows=[]
    def emit(st,k,off=None,lod=None,detail=""): rows.append({"status":st,"symbol":k[0],"action_type":k[1],"ex_date":k[2],"record_date":k[3],"official_cash_div":(off or {}).get("cash_dividend_per_share",""),"loaded_cash_div":(lod or {}).get("cash_dividend_per_share",""),"official_bonus":(off or {}).get("bonus_ratio",""),"loaded_bonus":(lod or {}).get("bonus_ratio",""),"detail":detail})
    conflict_keys={x[0] for x in conflicts}
    for k in sorted(missing): emit("MISSING_IN_PRODUCTION",k,official_map[k],None,"Official event not loaded in production")
    for k in sorted(extra): emit("EXTRA_IN_PRODUCTION",k,None,loaded_map[k],"Production event absent from independent official register")
    for k,off,lod in conflicts: emit("VALUE_CONFLICT",k,off,lod,"Event values differ from independent official register")
    for k in sorted(intersection-conflict_keys): emit("MATCHED",k,official_map[k],loaded_map[k],"Exact event-key/value match")
    with DIFF_FILE.open("w",encoding="utf-8-sig",newline="") as f:
        fields=["status","symbol","action_type","ex_date","record_date","official_cash_div","loaded_cash_div","official_bonus","loaded_bonus","detail"]; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    metrics={"official_register_valid":True,"official_source_dataset_id":status.get("source_dataset_id"),"expected_events":len(expected_set),"loaded_events":len(loaded_set),"matched_events":len(intersection)-len(conflicts),"missing_events":len(missing),"extra_events":len(extra),"conflicting_events":len(conflicts),"dataset_complete":bool(expected_set and not missing and not extra and not conflicts)}; print(json.dumps(metrics,indent=2,ensure_ascii=False)); return metrics

if __name__=="__main__": reconcile_corporate_actions()
