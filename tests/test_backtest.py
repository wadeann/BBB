from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from a_share_agent.backtest.engine import BacktestEngine
from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.backtest.report import BacktestReportWriter
from a_share_agent.config import load_config


class SyntheticProvider:
    def __init__(self, root: Path):
        self.root=root; self.warnings=[]; self._bars={}
        start=date(2024,1,1)
        dates=[]; d=start
        while len(dates)<650:
            if d.weekday()<5: dates.append(d.isoformat())
            d+=timedelta(days=1)
        bench=[]
        for i,ds in enumerate(dates):
            c=3000+i*5.0
            bench.append({"date":ds,"open":c-3,"high":c+8,"low":c-8,"close":c,"volume":1_000_000+i*100})
        self._bars["000300.SH"]=bench
        for j,sym in enumerate(["600001.SH","600002.SH"]):
            rows=[]; base=10+j
            for i,ds in enumerate(dates):
                # rising staircase with regular volume spikes. The post-spike advance
                # repeatedly creates high_volume_breakout evidence.
                c=base+i*0.012 + (i//35)*0.15
                o=c-0.015; h=c+0.04; l=c-0.05
                vol=3_500_000 if i%30==0 else 1_000_000
                rows.append({"date":ds,"open":o,"high":h,"low":l,"close":c,"volume":vol})
            self._bars[sym]=rows
    def bars(self,symbol,**kwargs): return self._bars.get(symbol,[])
    def sector_info(self,symbol): return {"name":"测试板块","code":None,"source":"synthetic"}
    def sector_bars(self,code): return []


def test_backtest_next_open_and_report(tmp_path: Path):
    # Copy config files from repository root by using actual project config.
    cfg=load_config(Path(__file__).resolve().parents[1])
    provider=SyntheticProvider(tmp_path)
    s=BacktestSettings(start_date="2025-01-01",end_date="2025-12-31",initial_cash=1_000_000,benchmark="000300.SH",min_score=75,max_holding_days=12)
    report=BacktestEngine(cfg,provider,s).run(["600001.SH","600002.SH"])
    assert report["methodology"]["entry_execution"] == "next_trading_day_open"
    assert report["methodology"]["llm_used"] is False
    assert report["coverage"]["tested_symbols"] == 2
    assert report["metrics"]["ending_equity"] > 0
    assert report["metrics"]["closed_trades"] > 0
    # Any BUY must happen strictly after signal date (no close look-ahead fill).
    for t in report["trades"]:
        if t["direction"]=="BUY": assert t["trade_date"] > t["signal_date"]
    w=BacktestReportWriter(tmp_path); path=w.write(report)
    assert (path/"report.json").exists(); assert (path/"report.html").exists(); assert (path/"equity_curve.csv").exists()


def test_backtest_settings_loaded():
    cfg=load_config(Path(__file__).resolve().parents[1])
    assert cfg.backtest["start_date"] == "2024-10-01"
