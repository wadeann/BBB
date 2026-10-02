from __future__ import annotations

from pathlib import Path

from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.llm_filter import HistoricalLLMFilter


class IntervalUniverseMCP:
    def __init__(self):
        self.calls=[]
    def invoke(self, tool, **kwargs):
        self.calls.append((tool, kwargs))
        if tool == "mcp_intel_get_historical_universe":
            if kwargs.get("mode") == "membership_intervals":
                return {
                    "point_in_time": True,
                    "dataset_version": "test-v1",
                    "data_quality": {"coverage": 1.0},
                    "data": [
                        {"symbol":"600001.SH","effective_from":"2025-01-01","effective_to":"2025-01-02","tradable":True,"industry_code":"BK1","industry_name":"行业A"},
                        {"symbol":"600002.SH","effective_from":"2025-01-02","effective_to":None,"tradable":True,"industry_code":"BK2","industry_name":"行业B"},
                    ],
                }
        raise RuntimeError(tool)


def test_interval_universe_rebuilds_each_day(tmp_path: Path):
    mcp=IntervalUniverseMCP()
    p=HistoricalDataProvider(tmp_path,mcp,use_cache=False)
    info=p.load_universe_for_period("2025-01-01","2025-01-03","missing.txt",mode="strict_point_in_time")
    assert info.point_in_time and info.dynamic_daily and not info.survivorship_bias
    assert p.active_symbols_on("2025-01-01",info.symbols)==["600001.SH"]
    assert p.active_symbols_on("2025-01-02",info.symbols)==["600001.SH","600002.SH"]
    assert p.active_symbols_on("2025-01-03",info.symbols)==["600002.SH"]
    sec=p.sector_info_on("600002.SH","2025-01-03",strict=True)
    assert sec["code"]=="BK2" and sec["source"] in {"historical_security_master","historical_universe"}


class DailyUniverseMCP:
    def invoke(self, tool, **kwargs):
        if tool != "mcp_intel_get_historical_universe":
            raise RuntimeError(tool)
        if kwargs.get("mode") == "membership_intervals":
            raise RuntimeError("interval mode unsupported")
        d=kwargs["date"]
        rows={
            "2025-01-02":[{"symbol":"600001.SH","tradable":True,"industry_code":"BK1","industry_name":"A"}],
            "2025-01-03":[{"symbol":"600002.SH","tradable":True,"industry_code":"BK2","industry_name":"B"}],
        }.get(d,[])
        if not rows:
            raise RuntimeError("INVALID_DATE")
        return {"trade_date":d,"point_in_time":True,"total":len(rows),"dataset_version":"daily-v1","data_quality":{"coverage":1.0},"data":rows}


def test_daily_universe_path_probes_past_holiday_and_changes(tmp_path: Path):
    p=HistoricalDataProvider(tmp_path,DailyUniverseMCP(),use_cache=False)
    info=p.load_universe_for_period("2025-01-01","2025-01-03","missing.txt",mode="strict_point_in_time")
    assert info.source=="mcp_historical_universe_daily"
    assert p.active_symbols_on("2025-01-02",info.symbols)==["600001.SH"]
    assert p.active_symbols_on("2025-01-03",info.symbols)==["600002.SH"]
    assert p.daily_universe_meta("2025-01-03",info.symbols)["point_in_time"] is True


class BrokenLLM:
    def complete_json(self, **kwargs):
        raise TimeoutError("simulated timeout")


def test_llm_failure_is_error_not_reject(tmp_path: Path):
    f=HistoricalLLMFilter(tmp_path,BrokenLLM(),model_id="broken",use_cache=False,batch_size=1)
    cand={"primary":{"signal":"single_bull_hold"},"score":80,"breakdown":{},"route":{},"market":{},"sector":{},"hits":[],"stop":9.5}
    bars=[{"date":f"2025-01-{i:02d}","open":10,"high":11,"low":9,"close":10.5,"volume":1000} for i in range(1,10)]
    out=f.decide(as_of="2025-01-09",symbol="600001.SH",candidate=cand,recent_bars=bars)
    assert out["decision"]=="ERROR"
    stats=f.stats()
    assert stats["failures"]==1 and stats["error_candidates"]==1 and stats["candidate_error_rate"]==1.0


def test_engine_report_tracks_dynamic_daily_universe(tmp_path: Path):
    import csv
    from datetime import date, timedelta
    from a_share_agent.backtest.engine import BacktestEngine
    from a_share_agent.backtest.models import BacktestSettings
    from a_share_agent.config import load_config

    bdir=tmp_path/"data"/"backtest"; pdir=bdir/"prices"
    pdir.mkdir(parents=True)
    dates=[]; d=date(2024,9,1)
    while len(dates)<120:
        if d.weekday()<5: dates.append(d.isoformat())
        d += timedelta(days=1)
    mid=dates[75]
    (bdir/"security_master.csv").write_text(
        "symbol,active_from,active_to,tradable,st,suspended,industry_code,industry_name\n"
        f"600001.SH,{dates[0]},{mid},1,0,0,BK1,A\n"
        f"600002.SH,{mid},,1,0,0,BK2,B\n", encoding="utf-8"
    )
    def write(sym, base):
        with (pdir/(sym.replace('.', '_')+'.csv')).open('w',encoding='utf-8',newline='') as fh:
            w=csv.DictWriter(fh,fieldnames=['date','open','high','low','close','volume'])
            w.writeheader()
            for i,ds in enumerate(dates):
                c=base+i*.01
                w.writerow({'date':ds,'open':c,'high':c+.1,'low':c-.1,'close':c+.02,'volume':1_000_000})
    write('000300.SH',3000); write('000852.SH',5000); write('399006.SZ',2000)
    write('600001.SH',10); write('600002.SH',20)
    provider=HistoricalDataProvider(tmp_path,None,use_cache=False)
    info=provider.load_universe_for_period(dates[65],dates[-1],"missing.txt",mode="strict_point_in_time")
    cfg=load_config(Path(__file__).resolve().parents[1])
    settings=BacktestSettings(start_date=dates[65],end_date=dates[-1],benchmark='000300.SH',universe_mode='strict_point_in_time',sector_mode='disabled')
    report=BacktestEngine(cfg,provider,settings).run(info.symbols)
    assert report['data_quality']['point_in_time_universe_all_days'] is True
    assert report['data_quality']['dynamic_universe_daily'] is True
    assert report['coverage']['dynamic_universe_days'] > 20
    assert report['coverage']['active_universe_max'] == 2
    assert report['coverage']['active_universe_min'] >= 1
