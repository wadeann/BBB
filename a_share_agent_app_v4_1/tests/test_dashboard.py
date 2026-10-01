from pathlib import Path

from fastapi.testclient import TestClient

from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.runtime import AgentRuntime
from a_share_agent.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_includes_sector_trend_and_limitup(runtime_root):
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    rt.run_phase("PREOPEN_CONTEXT")
    rt.run_phase("EOD_UNIVERSE_SCAN")
    client = TestClient(create_app(rt))
    resp = client.get("/api/dashboard?force=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["market"]["market_regime"] in {"risk_on", "neutral", "risk_off"}
    assert data["hot_sectors"]
    top = data["hot_sectors"][0]
    assert top["name"] == "机器人"
    assert top["trend"]["state"] in {"up", "range", "down", "unknown"}
    assert "route" in top and "allowed" in top["route"]
    assert data["limitup"]["max_streak"] == 6
    assert data["limitup"]["stocks"][0]["streak"] >= 4


def test_web_root_serves_console(runtime_root):
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    client = TestClient(create_app(rt))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "热门板块与趋势" in resp.text
    assert "连板梯队" in resp.text


def test_sector_detail_stock_detail_and_replay_workbench(runtime_root):
    mcp = FakeMCPInvoker()
    rt = AgentRuntime(runtime_root, mcp)
    rt.run_phase("PREOPEN_PRECHECK")
    rt.run_phase("PREOPEN_CONTEXT")
    rt.run_phase("EOD_UNIVERSE_SCAN")
    signal = {"symbol":"600000.SH","direction":"BUY","strategy_id":"ma60_breakout_retest","signal_version":"v1","decision":"ENTRY_CANDIDATE","score":84,"limit_price":10.0,"stop":9.6,"quantity":1000,"reason":"workbench test"}
    route = {"route_id":"RISK_ON_STRONG_SECTOR","position_multiplier":1.0}
    rt.execute_signal("ENTRY_WINDOW_AM", signal, route)
    rt.run_phase("CLOSE_RECONCILE")
    rt.run_phase("DAILY_REVIEW")
    client = TestClient(create_app(rt))

    sector = client.get("/api/sector/detail", params={"code":"BK_ROBOT","name":"机器人"})
    assert sector.status_code == 200
    sd = sector.json()
    assert sd["sector"]["name"] == "机器人"
    assert sd["sector"]["trend"]["state"] == "up"
    assert sd["limitup_stocks"] and sd["limitup_stocks"][0]["sector"] == "机器人"

    stock = client.get("/api/stock/600000.SH")
    assert stock.status_code == 200
    st = stock.json()
    assert st["symbol"] == "600000.SH"
    assert st["market_data"]["technical"]["ok"] is True
    assert st["audit"]["event_count"] >= 1

    trade_date = rt.audit.index.trade_dates()[0]
    replay = client.get(f"/api/replay/{trade_date}")
    assert replay.status_code == 200
    rd = replay.json()
    assert rd["verify"]["ok"] is True
    assert rd["event_count"] >= 1
    assert rd["traces"]

    trace_id = rd["traces"][0]["trace_id"]
    trace = client.get(f"/api/replay/{trade_date}/trace/{trace_id}")
    assert trace.status_code == 200
    assert trace.json()["event_count"] >= 1


def test_console_contains_detail_and_replay_surfaces(runtime_root):
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    client = TestClient(create_app(rt))
    html = client.get("/").text
    assert "交易研究与审计工作台" in html
    assert "Replay" in html
    assert "drawer" in html
