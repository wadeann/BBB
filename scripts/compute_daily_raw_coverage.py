#!/usr/bin/env python3
"""Compute per-trading-day Raw OHLCV coverage bound to the exact mounted Raw dataset hash."""
from __future__ import annotations
import csv,json,statistics
from datetime import datetime,timezone
from pathlib import Path
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.data_integrity import sha256_file,verify_raw_dataset_manifest
ROOT=Path(__file__).resolve().parent.parent; RAW_DIR=ROOT/"data"/"backtest"/"raw_prices"; BM_FILE=ROOT/"data"/"backtest"/"cache"/"bars"/"000300_SH.json"; OUTPUT_FILE=ROOT/"daily_raw_coverage.csv"; OUTPUT_MANIFEST=ROOT/"daily_raw_coverage_manifest.json"; RAW_MANIFEST=ROOT/"raw_dataset_manifest.json"; START,END="2024-10-01","2026-09-30"; BOARDS=["SSE_MAIN","STAR","SZSE_MAIN","CHINEXT","BSE"]

def _pct(num:int,den:int)->float: return num/den if den else 0.0

def compute_daily_raw_coverage():
    integrity=verify_raw_dataset_manifest(RAW_DIR,RAW_MANIFEST)
    if not integrity["raw_dataset_hash_match"]: raise RuntimeError("RAW_DATASET_HASH_MISMATCH_OR_UNMOUNTED: refusing to generate coverage from an unverified dataset")
    if not BM_FILE.exists(): raise RuntimeError(f"Benchmark trading calendar missing: {BM_FILE}")
    bm=json.loads(BM_FILE.read_text(encoding="utf-8")); trading_dates=sorted(str(b["date"]) for b in bm if START<=str(b.get("date",""))<=END)
    provider=HistoricalDataProvider(ROOT,mcp=None,use_cache=False); universe=provider.load_universe_for_period(START,END,"data/backtest/security_master.csv",mode="strict_point_in_time")
    raw_dates={}
    for path in RAW_DIR.glob("*.csv"):
        sym=path.stem.replace("_",".")
        with path.open("r",encoding="utf-8-sig") as fh: raw_dates[sym]={str(r.get("date") or "") for r in csv.DictReader(fh) if r.get("date")}
    rows=[]; active_covs=[]; tradable_covs=[]
    for d in trading_dates:
        active_records=provider.active_records_on(d,universe.symbols); tradable_records=[r for r in active_records if r.get("market_tradable")]
        active_symbols=[str(r["symbol"]) for r in active_records]; tradable_symbols=[str(r["symbol"]) for r in tradable_records]
        active_available=sum(1 for s in active_symbols if d in raw_dates.get(s,set())); tradable_available=sum(1 for s in tradable_symbols if d in raw_dates.get(s,set()))
        active_cov=_pct(active_available,len(active_symbols)); tradable_cov=_pct(tradable_available,len(tradable_symbols)); active_covs.append(active_cov); tradable_covs.append(tradable_cov)
        board_counts={b:[0,0] for b in BOARDS}
        for r in active_records:
            b=str(r.get("board") or "")
            if b not in board_counts: continue
            board_counts[b][0]+=1
            if d in raw_dates.get(str(r["symbol"]),set()): board_counts[b][1]+=1
        row={"date":d,"active_symbols":len(active_symbols),"active_raw_available":active_available,"active_raw_coverage":f"{active_cov*100:.2f}%","market_tradable_symbols":len(tradable_symbols),"tradable_raw_available":tradable_available,"tradable_raw_coverage":f"{tradable_cov*100:.2f}%"}
        col={"SSE_MAIN":"sse_main_cov","STAR":"star_cov","SZSE_MAIN":"szse_main_cov","CHINEXT":"chinext_cov","BSE":"bse_cov"}
        for b,c in col.items(): row[c]=f"{_pct(board_counts[b][1],board_counts[b][0])*100:.2f}%"
        rows.append(row)
    with OUTPUT_FILE.open("w",encoding="utf-8-sig",newline="") as fh:
        fields=["date","active_symbols","active_raw_available","active_raw_coverage","market_tradable_symbols","tradable_raw_available","tradable_raw_coverage","sse_main_cov","star_cov","szse_main_cov","chinext_cov","bse_cov"]; w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(rows)
    sorted_covs=sorted(active_covs); summary={"trading_days":len(rows),"min_daily_active_raw_coverage":min(active_covs) if active_covs else 0.0,"median_daily_active_raw_coverage":statistics.median(active_covs) if active_covs else 0.0,"p05_daily_active_raw_coverage":sorted_covs[max(0,int(len(sorted_covs)*0.05))] if sorted_covs else 0.0,"days_below_98pct":sum(1 for x in active_covs if x<0.98),"meets_formal_threshold":bool(active_covs and min(active_covs)>=0.98)}
    manifest={"coverage_version":"0.7.5","generated_at":datetime.now(timezone.utc).isoformat(),"source_raw_dataset_hash":integrity["actual_raw_dataset_hash"],"source_raw_file_count":integrity["actual_file_count"],"source_raw_row_count":integrity["actual_row_count"],"coverage_csv":OUTPUT_FILE.name,"coverage_csv_sha256":sha256_file(OUTPUT_FILE),"denominator_semantics":{"strict_gate":"active listed A-share common-equity securities","diagnostic":"market-tradable securities after PIT status/data-availability semantics"},"summary":summary}
    OUTPUT_MANIFEST.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8"); print(json.dumps(manifest,indent=2,ensure_ascii=False)); return summary

if __name__=="__main__": compute_daily_raw_coverage()
