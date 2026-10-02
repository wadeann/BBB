from __future__ import annotations

from pathlib import Path

from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.llm_filter import HistoricalLLMFilter
from a_share_agent.backtest.metrics import performance_metrics
from a_share_agent.backtest.research import research_validity


class FakeLLM:
    def __init__(self): self.calls=0
    def complete_json(self, *, system_prompt, user_payload, schema=None):
        self.calls += 1
        if isinstance(user_payload, dict) and isinstance(user_payload.get("candidates"), list):
            return {"decisions":[{
                "candidate_id":x["candidate_id"],"decision":"PASS","confidence":80,
                "reasons_for":["setup coherent"],"reasons_against":["normal uncertainty"],"risk_flags":[]
            } for x in user_payload["candidates"]]}
        return {"decision":"PASS","confidence":80,"reasons_for":["setup coherent"],"reasons_against":["normal uncertainty"],"risk_flags":[]}


def test_historical_llm_filter_is_cached(tmp_path: Path):
    llm=FakeLLM(); f=HistoricalLLMFilter(tmp_path,llm,model_id="test-model")
    candidate={"primary":{"signal":"single_bull_hold"},"score":82,"breakdown":{},"route":{},"market":{},"sector":{},"hits":[],"stop":9.5}
    bars=[{"date":f"2025-01-{i:02d}","open":10,"high":11,"low":9,"close":10.5,"volume":1000} for i in range(1,10)]
    a=f.decide(as_of="2025-01-09",symbol="600001.SH",candidate=candidate,recent_bars=bars)
    b=f.decide(as_of="2025-01-09",symbol="600001.SH",candidate=candidate,recent_bars=bars)
    assert a["decision"] == "PASS" and b["decision"] == "PASS"
    assert llm.calls == 1
    assert f.stats()["cache_hits"] == 1


def test_research_validity_marks_small_neutral_universe_diagnostic():
    class U:
        survivorship_bias=True
    report={
        "coverage":{"tested_symbols":63},
        "by_route":[{"group":"RISK_ON_NEUTRAL_SECTOR","trades":199},{"group":"NEUTRAL_NEUTRAL_SECTOR","trades":38}],
        "data_quality":{"historical_sector_membership_point_in_time":False,"sector_history_missing":[]},
    }
    out=research_validity(report,U(),{"min_symbols_for_research_grade":500,"max_neutral_sector_share_for_research_grade":0.85})
    assert out["grade"] == "DIAGNOSTIC_ONLY"
    assert any("tested_symbols" in x for x in out["reasons"])
    assert "universe_has_survivorship_bias" in out["reasons"]


def test_security_master_point_in_time_eligibility(tmp_path: Path):
    p=tmp_path/"data"/"backtest"; p.mkdir(parents=True)
    (p/"security_master.csv").write_text(
        "symbol,active_from,active_to,tradable,st,suspended\n"
        "600001.SH,2024-01-01,2024-12-31,1,0,0\n"
        "600002.SH,2025-01-01,,1,0,0\n", encoding="utf-8"
    )
    provider=HistoricalDataProvider(tmp_path,None)
    u=provider.load_universe_for_period("2024-01-01","2025-12-31","data/backtest/universe.txt",mode="strict_point_in_time")
    assert u.point_in_time is True and u.survivorship_bias is False
    assert provider.eligible_on("600001.SH","2024-06-01") is True
    assert provider.eligible_on("600001.SH","2025-06-01") is False
    assert provider.eligible_on("600002.SH","2024-06-01") is False
    assert provider.eligible_on("600002.SH","2025-06-01") is True


def test_performance_reports_gross_cost_net_decomposition():
    curve=[{"equity":1000000},{"equity":1000900}]
    trades=[
        {"direction":"BUY","fees":20},
        {"direction":"SELL","fees":30,"pnl":900,"pnl_pct":0.009,"gross_pnl_before_costs":1000,"round_trip_fees":50,"round_trip_slippage":50},
    ]
    m=performance_metrics(curve,trades,1000000)
    assert m["gross_pnl_before_costs"] == 1000
    assert m["round_trip_fees"] == 50
    assert m["estimated_slippage_cost"] == 50
    assert m["net_realized_pnl"] == 900


def test_research_suite_end_to_end_creates_feedback_bundle(runtime_root: Path):
    import csv, json, zipfile
    from datetime import date, timedelta
    from a_share_agent.backtest.research import ResearchLab
    from a_share_agent.config import load_config
    from a_share_agent.mcp.fake import FakeMCPInvoker

    price_root=runtime_root/"data"/"backtest"/"prices"; price_root.mkdir(parents=True,exist_ok=True)
    dates=[]; d=date(2024,1,1)
    while len(dates)<420:
        if d.weekday()<5: dates.append(d.isoformat())
        d+=timedelta(days=1)
    def write(sym, base, drift):
        path=price_root/(sym.replace('.', '_')+'.csv')
        with path.open('w',encoding='utf-8',newline='') as fh:
            w=csv.DictWriter(fh,fieldnames=['date','open','high','low','close','volume'])
            w.writeheader()
            for i,ds in enumerate(dates):
                c=base+i*drift
                w.writerow({'date':ds,'open':c-.01,'high':c+.04,'low':c-.04,'close':c,'volume':3_000_000 if i%30==0 else 1_000_000})
    write('000300.SH',3000,3.0); write('600001.SH',10,.01)
    cfg=load_config(runtime_root)
    lab=ResearchLab(cfg,FakeMCPInvoker(),None)
    out=lab.run(
        overrides={'start_date':'2025-01-01','end_date':'2025-06-30'},
        symbols=['600001.SH'], experiment_ids=['baseline','no_triple_golden_cross']
    )
    bundle=Path(out['feedback_bundle'])
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as z:
        names=set(z.namelist())
        assert 'research_summary.json' in names
        assert 'experiment_metrics.csv' in names
        assert any(n.endswith('/report.json') for n in names)


def test_llm_filter_transport_error_is_error_not_intelligent_reject(tmp_path: Path):
    class BadLLM:
        def complete_json(self, **kwargs):
            raise RuntimeError("timeout")
    f=HistoricalLLMFilter(tmp_path,BadLLM(),model_id="bad",batch_size=1)
    candidate={"primary":{"signal":"single_bull_hold"},"score":82,"breakdown":{},"route":{},"market":{},"sector":{},"hits":[],"stop":9.5}
    bars=[{"date":f"2025-01-{i:02d}","open":10,"high":11,"low":9,"close":10.5,"volume":1000} for i in range(1,10)]
    out=f.decide(as_of="2025-01-09",symbol="600001.SH",candidate=candidate,recent_bars=bars)
    assert out["decision"] == "ERROR"
    st=f.stats()
    assert st["failures"] == 1 and st["error_candidates"] == 1 and st["candidate_error_rate"] == 1.0


def test_research_validity_marks_llm_transport_failure_invalid():
    class U:
        survivorship_bias=False
    report={
        "settings":{"llm_filter_enabled":True},
        "coverage":{"tested_symbols":600},
        "by_route":[{"group":"RISK_ON_STRONG_SECTOR","trades":10}],
        "data_quality":{"historical_sector_membership_point_in_time":True,"sector_history_missing":[]},
        "methodology":{"llm_filter_stats":{"failures":1,"error_candidates":2,"candidates_reviewed":10}},
    }
    out=research_validity(report,U(),{"min_symbols_for_research_grade":500,"max_neutral_sector_share_for_research_grade":0.85})
    assert out["grade"] == "DIAGNOSTIC_ONLY"
    assert out["llm_experiment_valid"] is False
    assert any("llm_gate_transport_or_schema_errors" in x for x in out["reasons"])


def test_nested_tdx_f10_sector_parser():
    from a_share_agent.backtest.data import _extract_sector_fields
    raw={"success":True,"data":{"basic":{"所属行业":"半导体","行业代码":"BK1036"}}}
    name,code,diag=_extract_sector_fields(raw)
    assert name == "半导体" and code == "BK1036"
    assert "data" in diag["top_level_keys"]
