from datetime import datetime
from zoneinfo import ZoneInfo

from a_share_agent.config import load_config
from a_share_agent.core.permissions import PhasePermissions
from a_share_agent.core.scheduler import Scheduler
from a_share_agent.strategy.router import StrategyRouter


def test_scheduler_phase(runtime_root):
    cfg=load_config(runtime_root)
    sch=Scheduler(cfg.schedule, PhasePermissions(cfg.permissions))
    dt=datetime(2026,10,8,9,40,tzinfo=ZoneInfo("Asia/Shanghai"))
    assert sch.phase_at(dt)=="ENTRY_WINDOW_AM"


def test_router_risk_off(runtime_root):
    cfg=load_config(runtime_root)
    r=StrategyRouter(cfg.strategy_router).route(market_context={"market_regime":"risk_off","market_trend":"down","sentiment_phase":"panic"}, sector_context={"sector_strength":"strong","sector_lifecycle":"accelerating"})
    assert r["position_multiplier"]==0.25
    assert "trend_breakout" in r["blocked_strategy_families"]
