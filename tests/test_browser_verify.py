from __future__ import annotations

import os
from pathlib import Path
from fastapi.testclient import TestClient

from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.runtime import AgentRuntime
from a_share_agent.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _setup(runtime_root: Path) -> TestClient:
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    rt.run_phase("PREOPEN_CONTEXT")
    rt.run_phase("EOD_UNIVERSE_SCAN")
    return TestClient(create_app(rt))


class TestBrowserEndpointVerification:
    """Browser-based WebUI verification — all 6 v1 endpoints return valid JSON."""

    def test_v1_dashboard(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/dashboard?force=true")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "as_of" in d
        assert "market_regime" in d
        assert "risk_appetite" in d
        assert "themes" in d
        assert "enabled_patterns" in d
        assert "candidates_count" in d
        assert "positions_summary" in d
        assert "daily_pnl" in d
        assert "alerts" in d

    def test_v1_candidates(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/candidates?page=1&pattern=&regime=&sector=")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "trade_date" in d
        assert "page" in d
        assert d["page"] == 1
        assert "per_page" in d
        assert "total" in d
        assert "total_pages" in d

    def test_v1_positions(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/positions")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "as_of" in d
        assert "total_positions" in d
        assert "total_market_value" in d
        assert "available_cash" in d
        assert "gross_pnl" in d

    def test_v1_backtest(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/backtest?pattern=&regime=&lifecycle=&from=&to=")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "total" in d
        assert "results" in d
        assert isinstance(d["results"], list)

    def test_v1_trades(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/trades/0")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "round_trip_id" in d
        assert "trade" in d
        assert "events" in d
        assert "event_count" in d
        assert isinstance(d["events"], list)

    def test_v1_strategy_lab(self, runtime_root):
        client = _setup(runtime_root)
        resp = client.get("/api/v1/strategy-lab?pattern=&regime=")
        assert resp.status_code == 200
        d = resp.json()
        assert isinstance(d, dict)
        assert "comparison" in d
        assert isinstance(d["comparison"], list)
        assert "total_families" in d
        assert "total_runs" in d

    def test_all_v1_endpoints_return_valid_json(self, runtime_root):
        """All 6 endpoints respond with 200 and parseable JSON."""
        client = _setup(runtime_root)
        endpoints = [
            "/api/v1/dashboard?force=true",
            "/api/v1/candidates?page=1&pattern=&regime=&sector=",
            "/api/v1/positions",
            "/api/v1/backtest?pattern=&regime=&lifecycle=&from=&to=",
            "/api/v1/trades/0",
            "/api/v1/strategy-lab?pattern=&regime=",
        ]
        for ep in endpoints:
            resp = client.get(ep)
            assert resp.status_code == 200, f"{ep} returned {resp.status_code}"
            data = resp.json()
            assert data is not None, f"{ep} returned None"


class TestAuthMiddleware:
    """Auth middleware rejects POST without key when WEB_API_KEY is set."""

    def test_post_without_key_rejected(self, runtime_root):
        os.environ["WEB_API_KEY"] = "test-secret-key"
        try:
            client = _setup(runtime_root)
            resp = client.post("/api/backtests/run", json={"symbols": ["000001"]})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "invalid or missing API key"
        finally:
            os.environ.pop("WEB_API_KEY", None)

    def test_post_with_wrong_key_rejected(self, runtime_root):
        os.environ["WEB_API_KEY"] = "test-secret-key"
        try:
            client = _setup(runtime_root)
            resp = client.post(
                "/api/backtests/run",
                json={"symbols": ["000001"]},
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 403
        finally:
            os.environ.pop("WEB_API_KEY", None)

    def test_post_with_correct_key_allowed(self, runtime_root):
        os.environ["WEB_API_KEY"] = "test-secret-key"
        try:
            client = _setup(runtime_root)
            resp = client.post(
                "/api/backtests/run",
                json={"symbols": ["000001"]},
                headers={"X-API-Key": "test-secret-key"},
            )
            assert resp.status_code == 200
        finally:
            os.environ.pop("WEB_API_KEY", None)

    def test_get_never_requires_auth(self, runtime_root):
        """GET endpoints are never protected by auth middleware."""
        os.environ["WEB_API_KEY"] = "test-secret-key"
        try:
            client = _setup(runtime_root)
            resp = client.get("/api/v1/dashboard")
            assert resp.status_code == 200
        finally:
            os.environ.pop("WEB_API_KEY", None)
