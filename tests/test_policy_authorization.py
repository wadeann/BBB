"""Synthetic authorization contracts; not physical production provenance."""
from pathlib import Path
import pytest

from test_pattern_enablement import clock, physical, pending, approved, loader, KEY
from a_share_agent.strategy.router import StrategyRouter
from a_share_agent.runtime import AgentRuntime
from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.execution.engine import ExecutionRejected
from datetime import datetime, timezone
from a_share_agent.strategy.policy_loader import PolicyLoader


@pytest.fixture(autouse=True)
def runtime_policy_clock(clock, monkeypatch):
    class DecisionDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock[0], tz)
    monkeypatch.setattr("a_share_agent.core.scheduler.datetime", DecisionDatetime)


def contexts():
    return ({"regime": KEY[0], "market_regime": "risk_on", "data_quality": {"state": "ok"}},
            {"lifecycle": KEY[1], "sector_strength": "strong", "data_quality": {"state": "ok"}})


def hit():
    return {"signal": "P1", "pattern_id": KEY[2], "pattern_version": KEY[3], "family": "trend_breakout", "strength": "primary"}


DECISION_FIELDS = {"allowed", "reason", "key", "evidence_id", "manifest_hash", "policy_hash",
                   "policy_generation", "as_of", "quality", "status", "issued_at", "expires_at"}

def assert_decision(decision, allowed):
    assert set(decision) == DECISION_FIELDS
    assert decision["allowed"] is allowed
    assert decision["reason"]
    assert isinstance(decision["quality"], dict)

@pytest.mark.parametrize("state", ["allowed", "missing", "disabled", "invalid"])
def test_canonical_decision_metadata(loader, approved, clock, state):
    market, sector = contexts()
    publication = None
    if state != "missing":
        entry = approved.disable("manual") if state == "disabled" else approved
        publication = loader.publish_generation([entry], loader._directories[0])
        if state == "invalid":
            (Path(publication["generation_path"]) / "generation.json").write_text("{}")
    decision = loader.authorize_entry(hit(), market_context=market, sector_context=sector, as_of=clock[0])
    assert_decision(decision, state == "allowed")
    assert decision["key"] == list(KEY)
    assert decision["as_of"] == clock[0]
    if state in {"allowed", "disabled"}:
        assert decision["evidence_id"] == approved.evidence_id
        assert decision["manifest_hash"] == approved.manifest_hash
        assert decision["policy_generation"] == Path(publication["generation_path"]).name
        assert len(decision["policy_hash"]) == 64
        assert decision["status"] == ("approved" if state == "allowed" else "disabled")
    else:
        for name in ("evidence_id", "manifest_hash", "policy_hash", "policy_generation", "status", "issued_at", "expires_at"):
            assert decision[name] is None

@pytest.mark.parametrize("unsafe", ["PANIC", "UNKNOWN", "DEGRADED"])
def test_denied_unsafe_context_keeps_derivable_key(loader, clock, unsafe):
    market, sector = contexts()
    market["regime"] = unsafe
    decision = loader.authorize_entry(hit(), market_context=market, sector_context=sector, as_of=clock[0])
    assert_decision(decision, False)
    assert decision["key"] == [unsafe, *KEY[1:]]
    assert decision["reason"] == "unsafe_context"

def test_invalid_timestamp_keeps_derivable_key(loader):
    market, sector = contexts()
    decision = loader.authorize_entry(hit(), market_context=market, sector_context=sector, as_of=float("nan"))
    assert_decision(decision, False)
    assert decision["key"] == list(KEY)
    assert decision["as_of"] is None

@pytest.mark.parametrize("corruption", ["missing", "hash", "manifest"])
def test_history_falls_back_complete_prior(loader, approved, clock, corruption):
    first = loader.publish_generation([approved], loader._directories[0])
    clock[0] += 10
    latest = loader.publish_generation([approved.disable("manual")], loader._directories[0])
    gen = Path(latest["generation_path"])
    member = next(p for p in gen.glob("*.json") if p.name != "generation.json")
    if corruption == "missing": member.unlink()
    elif corruption == "hash": member.write_text("{}")
    else: (gen / "generation.json").write_text("{}")
    assert list(loader.load_active().values()) == [approved]
    assert loader.load_all() == [approved]
    assert list(loader.load_active(as_of=first["timestamp"]).values()) == [approved]
    assert loader.load_active(as_of=first["timestamp"] - 1) == {}
    (Path(first["generation_path"]) / "generation.json").write_text("{}")
    assert loader.load_active() == {}


@pytest.mark.parametrize("state", ["approved", "missing", "legacy", "pending", "expired", "future", "disabled", "revoked", "panic", "unknown", "degraded", "corrupt"])
def test_exact_authorization_and_exit_filter(loader, approved, pending, clock, state):
    entry = pending if state == "pending" else approved
    if state == "disabled": entry = approved.disable("manual")
    if state == "revoked": entry = approved.revoke("manual")
    published = loader.publish_generation([entry], loader._directories[0])
    market, sector = contexts(); signal = hit(); at = clock[0]
    if state == "missing": signal["pattern_version"] = "absent"
    if state == "legacy": signal.pop("pattern_version")
    if state == "expired": at = approved.expires_at
    if state == "future": at = approved.issued_at - 1
    if state == "panic": market["regime"] = "PANIC"
    if state == "unknown": sector["lifecycle"] = "UNKNOWN"
    if state == "degraded": market["data_quality"]["state"] = "degraded"
    if state == "corrupt": (Path(published["generation_path"]) / "generation.json").write_text("{}")
    decision = loader.authorize_entry(signal, market_context=market, sector_context=sector, as_of=at)
    assert decision["allowed"] is (state == "approved")
    assert decision["reason"]
    exit_hit = {"signal": "ma20_break", "strength": "exit", "family": "exit_defensive"}
    decisions = []
    filtered = loader.filter_signals([signal, exit_hit], market_context=market, sector_context=sector, as_of=at, decisions=decisions)
    assert len(decisions) == 1
    assert_decision(decisions[0], state == "approved")
    assert filtered == ([signal, exit_hit] if state == "approved" else [exit_hit])


def test_router_reports_and_blocks_missing_policy(loader, approved, clock):
    market, sector = contexts()
    cfg = {"precedence": ["R"], "routes": [{"id": "R", "allow": ["trend_breakout", "exit_defensive"], "position_multiplier": 1}]}
    router = StrategyRouter(cfg, policy_loader=loader)
    denied = router.route(market_context=market, sector_context=sector, signal=hit(), as_of=clock[0])
    assert denied["policy_decision"]["allowed"] is False
    assert_decision(denied["policy_decision"], False)
    assert denied["allowed_strategy_families"] == ["exit_defensive"]
    loader.publish_generation([approved], loader._directories[0])
    allowed = router.route(market_context=market, sector_context=sector, signal=hit(), as_of=clock[0])
    assert allowed["policy_decision"]["allowed"] is True
    assert_decision(allowed["policy_decision"], True)
    assert "trend_breakout" in allowed["allowed_strategy_families"]

@pytest.mark.parametrize("authorized", [False, True])
def test_router_persists_canonical_policy_audit(runtime_root, loader, approved, clock, authorized):
    import json
    from a_share_agent.audit.event_writer import AuditEventWriter
    from a_share_agent.config import load_config
    from a_share_agent.utils import as_trade_date
    if authorized:
        loader.publish_generation([approved], loader._directories[0])
    audit = AuditEventWriter(load_config(runtime_root))
    router = StrategyRouter({"precedence": ["R"], "routes": [{"id": "R", "allow": ["trend_breakout"]}]}, policy_loader=loader, audit=audit)
    market, sector = contexts()
    result = router.route(market_context=market, sector_context=sector, signal=hit(), as_of=clock[0])
    row = audit.index.latest(as_trade_date(), "POLICY_DECISION")
    assert row is not None
    assert json.loads(row["payload_json"])["policy_decision"] == result["policy_decision"]
    assert_decision(result["policy_decision"], authorized)


@pytest.mark.parametrize("authorized", [False, True])
def test_runtime_direct_buy_cannot_trust_route_label(runtime_root, loader, approved, authorized):
    if authorized: loader.publish_generation([approved], loader._directories[0])
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    market, sector = contexts()
    signal = dict(hit(), symbol="600000.SH", direction="BUY", limit_price=10., stop=9.6, quantity=1000,
                  regime=KEY[0], pattern_state=KEY[1], data_quality={"state": "ok"})
    route = {"position_multiplier": 1., "policy_decision": {"allowed": True}, "usable_for_router": True}
    if authorized:
        assert rt.execute_signal("ENTRY_WINDOW_AM", signal, route)["receipt"]["status"] == "FILLED"
        import json
        row = rt.audit.index.latest(__import__('a_share_agent.utils', fromlist=['as_trade_date']).as_trade_date(), "RISK_INTENT")
        assert_decision(json.loads(row["payload_json"])["route"]["policy_decision"], True)
        loader.publish_generation([approved.disable("manual")], loader._directories[0])
        signal["direction"] = "SELL"
        rt.mcp.positions = [{"symbol": signal["symbol"], "available_quantity": 1000}]
        assert rt.execute_signal("ENTRY_WINDOW_AM", signal, route)["receipt"]["status"] == "FILLED"
    else:
        with pytest.raises(ExecutionRejected, match="policy"):
            rt.execute_signal("ENTRY_WINDOW_AM", signal, route)
        import json
        row = rt.audit.index.latest(__import__('a_share_agent.utils', fromlist=['as_trade_date']).as_trade_date(), "POLICY_BLOCK")
        assert_decision(json.loads(row["payload_json"])["policy_decision"], False)


@pytest.mark.parametrize("trust", [False, True])
def test_enabled_config_constructs_loader_and_blocks_without_evidence(runtime_root, monkeypatch, trust):
    import yaml
    path = runtime_root / "config" / "strategy_router.yaml"
    cfg = yaml.safe_load(path.read_text()); cfg.setdefault("policy", {})["enabled"] = True
    path.write_text(yaml.safe_dump(cfg))
    for name, value in (("BBB_POLICY_INTEGRITY_KEY", "deployment-test-key-at-least-32-bytes"),
                        ("BBB_POLICY_TRUSTED_IDENTITY", "deployment-test-authority")):
        if trust: monkeypatch.setenv(name, value)
        else: monkeypatch.delenv(name, raising=False)
    rt = AgentRuntime(runtime_root, FakeMCPInvoker())
    assert isinstance(rt.policy_loader, PolicyLoader)
    assert rt.execution.policy_loader is rt.policy_loader
    assert rt.orchestrator.policy_loader is rt.policy_loader
    assert rt.policy_loader.load_active() == {}
    with pytest.raises(ExecutionRejected, match="policy"):
        rt.execute_signal("ENTRY_WINDOW_AM", dict(hit(), symbol="600000.SH", quantity=100, limit_price=10,
                          regime=KEY[0], pattern_state=KEY[1], data_quality={"state": "ok"}), {"position_multiplier": 1})

@pytest.mark.parametrize("authorization", ["missing", "approved", "revoked_before_fill"])
def test_backtest_scanner_and_fill_gate_with_router_disabled(runtime_root, loader, approved, clock, monkeypatch, authorization):
    from datetime import datetime, timezone
    from test_backtest import SyntheticProvider
    from a_share_agent.config import load_config
    from a_share_agent.backtest.engine import BacktestEngine
    from a_share_agent.backtest.models import BacktestSettings
    import a_share_agent.backtest.engine as module
    provider = SyntheticProvider(runtime_root)
    start = datetime.fromtimestamp(clock[0] + 86400, timezone.utc).date().isoformat()
    end = datetime.fromtimestamp(clock[0] + 4 * 86400, timezone.utc).date().isoformat()
    for bars in provider._bars.values():
        for i, bar in enumerate(bars):
            bar["date"] = datetime.fromtimestamp(clock[0] + (i - 300) * 86400, timezone.utc).date().isoformat()
    if authorization != "missing": loader.publish_generation([approved], loader._directories[0])
    market, sector = contexts()
    market["as_of"] = start
    monkeypatch.setattr(module, "market_context_from_benchmarks", lambda *a, **k: market)
    monkeypatch.setattr(module, "deterministic_score", lambda *a: (100, {}))
    engine = BacktestEngine(load_config(runtime_root), provider, BacktestSettings(start_date=start, end_date=end, min_score=0, route_mode="disabled"), policy_loader=loader)
    engine._sector_context = lambda *a: sector
    engine.signal_engine.scan = lambda *a, **k: [hit()]
    if authorization == "revoked_before_fill":
        clock[0] += 2 * 86400
        clock[0] -= 8 * 3600 - 1
        loader.publish_generation([approved.disable("manual")], loader._directories[0])
    report = engine.run(["600001.SH"])
    buys = [t for t in report["trades"] if t["direction"] == "BUY"]
    assert bool(buys) is (authorization == "approved")
    if authorization == "approved": assert any(t["direction"] == "SELL" for t in report["trades"])
    else: assert any(r["reason"] == "POLICY_BLOCK" for r in report["rejections"])
    entries = [e for e in report["events"] if e["type"] == "ENTRY_SIGNAL"]
    if authorization != "missing":
        assert entries
        for event in entries:
            assert_decision(event["policy_decision"], True)
    blocks = [r for r in report["rejections"] if r["reason"] == "POLICY_BLOCK"]
    for block in blocks:
        assert_decision(block["policy_decision"], False)
    block_events = [e for e in report["events"] if e["type"] == "POLICY_BLOCK"]
    assert len(block_events) == len(blocks)
    for event in block_events:
        assert_decision(event["policy_decision"], False)
    import json
    from a_share_agent.audit.event_writer import AuditEventWriter
    audit = AuditEventWriter(engine.cfg)
    records = audit.index.conn.execute("SELECT payload_json FROM events WHERE event_type = 'POLICY_DECISION'").fetchall()
    decisions = [json.loads(row[0])["policy_decision"] for row in records]
    assert decisions
    assert any(decision["allowed"] for decision in decisions) is (authorization != "missing")
    assert any(not decision["allowed"] for decision in decisions) is (authorization != "approved")
    for decision in decisions:
        assert_decision(decision, decision["allowed"])

def test_live_scanner_missing_context_blocks_entry_preserves_exit(runtime_root, loader, approved):
    loader.publish_generation([approved], loader._directories[0])
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    exit_hit = {"signal": "ma20_break", "strength": "exit", "family": "exit_defensive"}
    rt.orchestrator.signal_engine.scan = lambda *a, **k: [hit(), exit_hit]
    rt.run_phase("EOD_DEEP_DIVE", symbols=["600000.SH"])
    row = rt.audit.index.latest(__import__('a_share_agent.utils', fromlist=['as_trade_date']).as_trade_date(), "DEEP_DIVE_REPORT")
    import json
    assert json.loads(row["payload_json"])["deterministic_signals"] == [exit_hit]
    decisions = json.loads(row["payload_json"])["policy_decisions"]
    assert len(decisions) == 1
    assert_decision(decisions[0], False)
    policy_row = rt.audit.index.latest(__import__('a_share_agent.utils', fromlist=['as_trade_date']).as_trade_date(), "POLICY_DECISION")
    assert policy_row is not None
    assert json.loads(policy_row["payload_json"])["policy_decision"] == decisions[0]


def test_runtime_stale_signal_cannot_bypass_current_expiry(runtime_root, loader, approved, clock):
    loader.publish_generation([approved], loader._directories[0])
    clock[0] = approved.expires_at
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    signal = dict(hit(), symbol="600000.SH", quantity=100, limit_price=10, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"}, as_of=approved.issued_at)
    with pytest.raises(ExecutionRejected, match="policy"):
        rt.execute_signal("ENTRY_WINDOW_AM", signal, {"position_multiplier": 1})


@pytest.mark.parametrize("timestamp_source", ["numeric_signal", "iso_signal", "run_decision"])
def test_direct_buy_rejects_policy_created_after_decision(runtime_root, loader, approved, clock, timestamp_source):
    published = loader.publish_generation([approved], loader._directories[0])
    market, sector = contexts()
    assert loader.authorize_entry(hit(), market_context=market, sector_context=sector, as_of=clock[0])["allowed"]
    at = published["timestamp"] - 1
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    run = rt.scheduler.make_run_context("ENTRY_WINDOW_AM")
    signal = dict(hit(), symbol="600000.SH", quantity=1000, limit_price=10, stop=9.6, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"})
    if timestamp_source == "numeric_signal": signal["as_of"] = at
    elif timestamp_source == "iso_signal": signal["as_of"] = datetime.fromtimestamp(at, timezone.utc).isoformat()
    else: run.as_of = datetime.fromtimestamp(at, timezone.utc).isoformat()
    with pytest.raises(ExecutionRejected, match="policy"):
        rt.execution.execute_signal(run, signal, {"position_multiplier": 1})
    assert rt.mcp.orders == []


def test_runtime_rejects_client_future_timestamp_even_with_valid_current_policy(runtime_root, loader, approved, clock):
    loader.publish_generation([approved], loader._directories[0])
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    signal = dict(hit(), symbol="600000.SH", quantity=1000, limit_price=10, stop=9.6, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"}, as_of=clock[0] + 1)
    with pytest.raises(ExecutionRejected, match="policy"):
        rt.execute_signal("ENTRY_WINDOW_AM", signal, {"position_multiplier": 1})


@pytest.mark.parametrize("as_of", [None, True, float("nan"), float("inf"), "2033-05-18T03:33:20", "not-a-date"])
def test_runtime_invalid_decision_timestamp_fails_closed(runtime_root, loader, approved, as_of):
    loader.publish_generation([approved], loader._directories[0])
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    signal = dict(hit(), symbol="600000.SH", quantity=1000, limit_price=10, stop=9.6, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"}, as_of=as_of)
    with pytest.raises(ExecutionRejected, match="policy rejected: invalid decision timestamp"):
        rt.execute_signal("ENTRY_WINDOW_AM", signal, {"position_multiplier": 1})
    assert rt.mcp.orders == []
    import json
    row = rt.audit.index.latest(__import__('a_share_agent.utils', fromlist=['as_trade_date']).as_trade_date(), "POLICY_BLOCK")
    decision = json.loads(row["payload_json"])["policy_decision"]
    assert_decision(decision, False)
    assert decision["key"] == list(KEY)
    assert decision["as_of"] is None


def test_runtime_stale_signal_cannot_bypass_current_revocation(runtime_root, loader, approved, clock):
    loader.publish_generation([approved], loader._directories[0])
    signal_at = clock[0]
    clock[0] += 1
    loader.publish_generation([approved.revoke("manual")], loader._directories[0])
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    signal = dict(hit(), symbol="600000.SH", quantity=1000, limit_price=10, stop=9.6, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"}, as_of=signal_at)
    with pytest.raises(ExecutionRejected, match="policy"):
        rt.execute_signal("ENTRY_WINDOW_AM", signal, {"position_multiplier": 1})
    assert rt.mcp.orders == []


@pytest.mark.parametrize("iso_timestamp", [False, True])
def test_runtime_valid_historical_signal_remains_authorized(runtime_root, loader, approved, clock, iso_timestamp):
    loader.publish_generation([approved], loader._directories[0])
    signal_at = clock[0]
    clock[0] += 1
    rt = AgentRuntime(runtime_root, FakeMCPInvoker(), policy_loader=loader)
    signal = dict(hit(), symbol="600000.SH", quantity=1000, limit_price=10, stop=9.6, regime=KEY[0], pattern_state=KEY[1],
                  data_quality={"state": "ok"},
                  as_of=datetime.fromtimestamp(signal_at, timezone.utc).isoformat() if iso_timestamp else signal_at)
    assert rt.execute_signal("ENTRY_WINDOW_AM", signal, {"position_multiplier": 1})["receipt"]["status"] == "FILLED"
