from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.runtime import AgentRuntime
from a_share_agent.utils import now_shanghai
from a_share_agent.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _setup(runtime_root: Path) -> TestClient:
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    rt.run_phase("PREOPEN_CONTEXT")
    rt.run_phase("EOD_UNIVERSE_SCAN")
    return TestClient(create_app(rt))


class TestV1Dashboard:
    def test_get_full_dashboard_includes_all_keys(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/dashboard?force=true")
        assert resp.status_code == 200
        d = resp.json()
        assert "as_of" in d
        assert d["market_regime"] in {"risk_on", "neutral", "risk_off"}
        assert "risk_appetite" in d
        assert isinstance(d["themes"], list)
        assert isinstance(d["enabled_patterns"], list)
        assert isinstance(d["candidates_count"], int)
        assert "positions_summary" in d
        assert "total_positions" in d["positions_summary"]
        assert "daily_pnl" in d
        assert "pnl" in d["daily_pnl"]
        assert isinstance(d["alerts"], list)

    def test_get_full_dashboard_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/dashboard?force=true")
        d = resp.json()
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw

    def test_get_full_dashboard_alerts_from_audit(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/dashboard?force=true")
        d = resp.json()
        for alert in d["alerts"]:
            assert "event_time" in alert
            assert "event_type" in alert


class TestV1Candidates:
    def test_get_candidates_default_page(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates")
        assert resp.status_code == 200
        d = resp.json()
        assert "page" in d
        assert d["page"] == 1
        assert "total_pages" in d
        assert "per_page" in d
        assert "total" in d
        assert isinstance(d["items"], list)
        assert "trade_date" in d

    def test_get_candidates_with_pattern_filter(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates?pattern=trend_breakout")
        assert resp.status_code == 200
        d = resp.json()
        assert d["filters_applied"]["pattern_family"] == "trend_breakout"

    def test_get_candidates_no_duplicate_within_page(self, runtime_root):
        """No duplicate symbols within a single page."""
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates")
        assert resp.status_code == 200
        d = resp.json()
        symbols = [x.get("symbol", "") for x in d["items"]]
        assert len(symbols) == len(set(symbols)), "duplicate symbols on page"

    def test_get_candidates_empty_state(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates?pattern=NONEXISTENT_PATTERN_XYZ")
        assert resp.status_code == 200
        d = resp.json()
        assert d["total"] == 0
        assert d["items"] == []

    def test_get_candidates_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates")
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw


class TestV1Positions:
    def test_get_positions_returns_schema(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/positions")
        assert resp.status_code == 200
        d = resp.json()
        assert "as_of" in d
        assert "total_positions" in d
        assert isinstance(d["total_positions"], int)
        assert "total_market_value" in d
        assert "available_cash" in d
        assert "gross_pnl" in d
        assert "positions" in d
        assert isinstance(d["positions"], list)

    def test_get_positions_each_item_has_required_keys(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/positions")
        d = resp.json()
        for p in d["positions"]:
            assert "symbol" in p
            assert "quantity" in p
            assert "cost_price" in p
            assert "current_price" in p
            assert "market_value" in p
            assert "pnl" in p
            assert "pnl_percent" in p

    def test_get_positions_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/positions")
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw


class TestV1Backtest:
    def test_get_backtest_results_returns_schema(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/backtest")
        assert resp.status_code == 200
        d = resp.json()
        assert "total" in d
        assert isinstance(d["total"], int)
        assert "results" in d
        assert isinstance(d["results"], list)

    def test_get_backtest_results_with_date_filter(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/backtest?from=2025-01-01&to=2025-12-31")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d["results"], list)

    def test_get_backtest_results_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/backtest")
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw


class TestV1TradeDetail:
    def test_get_trade_detail_returns_schema(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/trades/some-trade-id")
        assert resp.status_code == 200
        d = resp.json()
        assert "round_trip_id" in d
        assert "trade" in d or d.get("trade") is None
        assert isinstance(d["events"], list)
        assert isinstance(d["event_count"], int)

    def test_get_trade_detail_events_are_ordered(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/trades/test-trade-001")
        assert resp.status_code == 200
        d = resp.json()
        times = [e.get("event_time", "") for e in d["events"]]
        assert times == sorted(times), "events not sorted by time"

    def test_get_trade_detail_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/trades/test-trade-001")
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw


class TestV1StrategyLab:
    def test_get_strategy_lab_returns_schema(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/strategy-lab")
        assert resp.status_code == 200
        d = resp.json()
        assert "comparison" in d
        assert isinstance(d["comparison"], list)
        assert "total_families" in d
        assert "total_runs" in d

    def test_get_strategy_lab_each_comparison_has_family_regime(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/strategy-lab")
        d = resp.json()
        for c in d["comparison"]:
            assert "family" in c
            assert "regime" in c
            assert "run_count" in c
            assert "avg_total_return" in c
            assert "avg_sharpe" in c
            assert "avg_max_drawdown" in c
            assert "avg_win_rate" in c
            assert "runs" in c
            assert isinstance(c["runs"], list)

    def test_get_strategy_lab_not_sorted_by_total_return(self, runtime_root):
        """G05: fold comparison, not sorted by total return."""
        client = _setup(runtime_root)
        resp = client.get("/api/v1/strategy-lab")
        d = resp.json()
        returns = [c.get("avg_total_return", 0) for c in d["comparison"]]
        # At least 2 entries are needed to verify no sort; skip when too few
        if len(returns) >= 2:
            assert returns != sorted(returns, reverse=True), "strategies should not be sorted by total return"

    def test_get_strategy_lab_no_nan_infinity(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/strategy-lab")
        raw = resp.text
        assert "NaN" not in raw and "Infinity" not in raw and "-Infinity" not in raw
