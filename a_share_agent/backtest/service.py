from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..mcp.base import MCPInvoker
from .data import HistoricalDataProvider
from .engine import BacktestEngine
from .models import BacktestSettings
from .report import BacktestReportWriter


def settings_from(config: RuntimeConfig, overrides: dict[str,Any] | None=None) -> BacktestSettings:
    allowed={f.name for f in fields(BacktestSettings)}
    raw={k:v for k,v in (config.backtest or {}).items() if k in allowed}
    for k,v in (overrides or {}).items():
        if k in allowed and v is not None: raw[k]=v
    return BacktestSettings(**raw)


class BacktestService:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker | None):
        self.config=config; self.mcp=mcp
        self.writer=BacktestReportWriter(config.project_root)
        self.job_root=config.project_root/"data"/"backtest"/"jobs"; self.job_root.mkdir(parents=True,exist_ok=True)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="backtest")
        self.lock=threading.Lock(); self.jobs: dict[str,dict[str,Any]]={}

    def _job_file(self,jid:str)->Path:return self.job_root/f"{jid}.json"
    def _save_job(self,j:dict[str,Any])->None:
        self.jobs[j["job_id"]]=j; self._job_file(j["job_id"]).write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")

    def submit(self, overrides: dict[str,Any] | None=None, symbols: list[str] | None=None, *, walk_forward: bool=False) -> dict[str,Any]:
        jid=f"job-{uuid.uuid4().hex[:12]}"; j={"job_id":jid,"status":"QUEUED","created_at":datetime.now().astimezone().isoformat(),"overrides":overrides or {},"symbol_count":len(symbols or []),"walk_forward":walk_forward}
        self._save_job(j); self.pool.submit(self._run_job,jid,overrides or {},symbols or []); return j

    def _run_job(self,jid:str,overrides:dict[str,Any],symbols:list[str])->None:
        j=self.get_job(jid) or {"job_id":jid}; j.update(status="RUNNING",started_at=datetime.now().astimezone().isoformat()); self._save_job(j)
        try:
            s=settings_from(self.config,overrides); provider=HistoricalDataProvider(self.config.project_root,self.mcp,use_cache=s.cache)
            uni=None
            if not symbols:
                uni=provider.load_universe_for_period(s.start_date,s.end_date,s.universe_file,max_universe=s.max_universe,mode=s.universe_mode); symbols=uni.symbols
            if not symbols: raise RuntimeError("backtest universe is empty; populate data/backtest/universe.txt or use production MCP universe")
            def run_one(ss:BacktestSettings):
                e=BacktestEngine(self.config,provider,ss); r=e.run(symbols)
                if uni:
                    r["universe"]={"source":uni.source,"survivorship_bias":uni.survivorship_bias,"notes":uni.notes,"seed_symbols":len(symbols),"tested_union_symbols":r.get("coverage",{}).get("tested_symbols",0),"point_in_time":uni.point_in_time,"membership_records":uni.membership_records,"dynamic_daily":uni.dynamic_daily,"dataset_version":uni.dataset_version,"coverage":uni.coverage}
                return r
            report=run_one(s); path=self.writer.write(report)
            j.update(status="COMPLETED",completed_at=datetime.now().astimezone().isoformat(),run_id=report["run_id"],report_dir=str(path),metrics=report.get("metrics"),coverage=report.get("coverage"))
        except Exception as exc:
            j.update(status="FAILED",completed_at=datetime.now().astimezone().isoformat(),error=f"{type(exc).__name__}: {exc}")
        self._save_job(j)

    def get_job(self,jid:str)->dict[str,Any]|None:
        if jid in self.jobs:return self.jobs[jid]
        p=self._job_file(jid)
        if p.exists():
            try:return json.loads(p.read_text(encoding="utf-8"))
            except Exception:return None
        return None

    def list_runs(self,limit:int=100)->list[dict[str,Any]]: return self.writer.list_runs(limit)
    def load_run(self,run_id:str)->dict[str,Any]|None:return self.writer.load(run_id)
