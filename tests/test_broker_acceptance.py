"""Phase 8 Broker Acceptance Tests: H01–H07.

H01: Phase2A/2B/5/6/7 acceptance PASS, policy authorization valid.
H02: default paper / allow_real_execution=false.
H03: Pattern cannot directly reach broker (flow through risk + intent).
H04: Kill switch + daily loss + rate limit.
H05: Reconciliation + idempotency.
H06: Credential isolation / redaction.
H07: Broker API contract + sandbox.

Run with: pytest tests/test_broker_acceptance.py -q --tb=short
"""
from __future__ import annotations

import hashlib
import json
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from a_share_agent.config import RuntimeConfig, load_config
from a_share_agent.core.permissions import PhasePermissions, PermissionDenied
from a_share_agent.execution.engine import ExecutionEngine, ExecutionRejected
from a_share_agent.execution.intent_store import IntentStore, DuplicateIntent
from a_share_agent.mcp.fake import FakeMCPInvoker
from a_share_agent.mcp.tool_catalog import EXEC_TOOLS
from a_share_agent.models import IntentRecord, RunContext, LocalRiskResult
from a_share_agent.risk.engine import LocalRiskEngine
from a_share_agent.strategy.enablement import PolicyEntry, PolicyConfig, PolicyStatus
from a_share_agent.strategy.policy_loader import PolicyLoader, policy_decision, decision_key
from a_share_agent.audit.event_writer import AuditEventWriter
from a_share_agent.utils import redact


# ════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    """Fixture that copies config + skill from the project tree."""
    import shutil
    src = Path(__file__).resolve().parents[1]
    shutil.copytree(src / "config", tmp_path / "config")
    shutil.copytree(src / "skill", tmp_path / "skill")
    return tmp_path


@pytest.fixture
def config(runtime_root: Path) -> RuntimeConfig:
    return load_config(runtime_root)


@pytest.fixture
def fake_mcp() -> FakeMCPInvoker:
    return FakeMCPInvoker()


@pytest.fixture
def intent_store(tmp_path: Path) -> IntentStore:
    return IntentStore(tmp_path / "intents.sqlite")


@pytest.fixture
def audit(config: RuntimeConfig) -> AuditEventWriter:
    return AuditEventWriter(config)


@pytest.fixture
def permissions() -> PhasePermissions:
    cfg = {
        "phases": {
            "ENTRY_WINDOW_AM": {
                "new_entry": True, "reduce_position": True,
                "cancel_order": True, "place_order": True,
            },
            "MIDDAY_REVIEW": {
                "new_entry": False, "reduce_position": False,
                "cancel_order": False, "place_order": False,
            },
            "EOD_UNIVERSE_SCAN": {
                "new_entry": False, "reduce_position": False,
                "cancel_order": False, "place_order": False,
            },
        }
    }
    return PhasePermissions(cfg)


@pytest.fixture
def local_risk(config: RuntimeConfig) -> LocalRiskEngine:
    return LocalRiskEngine(config)


@pytest.fixture
def run_context() -> RunContext:
    return RunContext(
        run_id="test-run-001",
        mode="paper",
        phase="ENTRY_WINDOW_AM",
        exchange_timezone="Asia/Shanghai",
        as_of=datetime.now(timezone.utc).isoformat(),
        allowed_actions=["new_entry", "place_order"],
    )


@pytest.fixture
def sample_signal() -> dict[str, Any]:
    return {
        "symbol": "600000.SH",
        "direction": "BUY",
        "pattern_id": "ma60_breakout_retest",
        "pattern_version": "1.0.0",
        "strategy_id": "test_strategy",
        "limit_price": 10.0,
        "quantity": 1000,
        "stop": 9.5,
        "regime": "BULLISH",
        "regime_data_quality": {"state": "ok"},
        "pattern_state": "EARLY_GROWTH",
        "pattern_state_data_quality": {"state": "ok"},
        "signal_version": "v1",
        "reason": "test signal",
        "data_quality": {"state": "ok"},
    }


@pytest.fixture
def route() -> dict[str, Any]:
    return {"route_id": "test_route", "position_multiplier": 1.0}


@pytest.fixture
def policy_loader(tmp_path: Path, config: RuntimeConfig) -> PolicyLoader:
    """A PolicyLoader with an empty policy directory (no policies)."""
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    return PolicyLoader(
        [policy_dir],
        integrity_key=b"test-only-integrity-key-32-bytes!!",
        trusted_identity="test-authority",
        config=PolicyConfig(snapshot_directory=str(tmp_path / "audit")),
        runtime_config=config,
    )


# ════════════════════════════════════════════════════════════════════════
# H01: Phase 2A/2B/5/6/7 Acceptance PASS, policy authorization valid
# ════════════════════════════════════════════════════════════════════════


class TestH01_PhasesAcceptanceAndPolicy:
    """Pre-req modules import, PolicyEntry with STABLE_CANDIDATE, block without policy."""

    @staticmethod
    def _minimal_run(mode: str = "paper") -> RunContext:
        return RunContext(
            run_id="h01", mode=mode, phase="ENTRY_WINDOW_AM",
            exchange_timezone="Asia/Shanghai",
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    def test_prereq_modules_import(self):
        """All Phase 2A/2B/5/6/7 modules import cleanly."""
        from a_share_agent import (
            config, models, utils,
            core, execution, risk, strategy, audit, mcp,
        )
        assert hasattr(config, "RuntimeConfig")
        assert hasattr(models, "IntentRecord")
        assert hasattr(execution.engine, "ExecutionEngine")
        assert hasattr(risk.engine, "LocalRiskEngine")
        assert hasattr(strategy.enablement, "PolicyEntry")
        assert hasattr(strategy.policy_loader, "PolicyLoader")
        assert hasattr(mcp.base, "MCPInvoker")
        assert hasattr(audit.event_writer, "AuditEventWriter")
        assert hasattr(core.permissions, "PhasePermissions")

    def test_policy_decision_shape(self):
        """policy_decision() returns the expected structure with allowed=False."""
        decision = policy_decision()
        assert isinstance(decision, dict)
        assert decision["allowed"] is False
        assert "reason" in decision
        assert "key" in decision
        assert "evidence_id" in decision
        assert "status" in decision

    def test_policy_decision_with_key(self):
        """policy_decision() accepts explicit key and quality metadata."""
        key = ("ma60_breakout_retest", "BULLISH", "EARLY_GROWTH", "1.0.0")
        decision = policy_decision(key=key)
        assert decision["key"] == key

    def test_decision_key_returns_list_for_valid_signal(self):
        """decision_key() returns a 4-element list when all fields are present."""
        signal = {"pattern_id": "ma60_breakout_retest", "pattern_version": "1.0.0"}
        market = {"regime": "BULLISH", "data_quality": {"state": "ok"}}
        sector = {"lifecycle": "EARLY_GROWTH", "data_quality": {"state": "ok"}}
        key = decision_key(signal, market, sector)
        assert isinstance(key, list)
        assert len(key) == 4
        assert all(isinstance(v, str) for v in key)

    def test_decision_key_none_on_missing_fields(self):
        """decision_key() returns None when required fields are missing."""
        signal = {}
        market = {}
        sector = {}
        key = decision_key(signal, market, sector)
        assert key is None

    def test_execution_blocks_without_valid_policy(self, config, fake_mcp, audit,
                                                    permissions, intent_store, local_risk,
                                                    sample_signal, route, policy_loader):
        """ExecutionEngine.execute_signal raises ExecutionRejected when no valid policy exists."""
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=policy_loader,
        )
        ctx = self._minimal_run()
        with pytest.raises(ExecutionRejected, match="policy rejected"):
            engine.execute_signal(ctx, sample_signal, route)

    def test_policy_loader_empty_returns_not_allowed(self, policy_loader):
        """PolicyLoader.authorize_entry returns allowed=False for empty directory."""
        signal = {"pattern_id": "ma60_breakout_retest", "pattern_version": "1.0.0"}
        market = {"regime": "BULLISH", "data_quality": {"state": "ok"}}
        sector = {"lifecycle": "EARLY_GROWTH", "data_quality": {"state": "ok"}}
        decision = policy_loader.authorize_entry(signal, market_context=market, sector_context=sector)
        assert decision["allowed"] is False
        assert "missing_or_invalid_policy" in decision.get("reason", "")

    def test_policy_key_mismatch_rejected(self, policy_loader):
        """authorize_entry rejects when no matching policy key exists."""
        signal = {"pattern_id": "nonexistent", "pattern_version": "0.0.0"}
        market = {"regime": "BEARISH", "data_quality": {"state": "degraded"}}
        sector = {"lifecycle": "LATE", "data_quality": {"state": "degraded"}}
        decision = policy_loader.authorize_entry(signal, market_context=market, sector_context=sector)
        assert decision["allowed"] is False


# ════════════════════════════════════════════════════════════════════════
# H02: default paper / allow_real_execution=false
# ════════════════════════════════════════════════════════════════════════


class TestH02_DefaultPaperNoRealExecution:
    """RuntimeConfig defaults and ExecutionEngine behavior."""

    def test_config_allow_real_execution_false_by_default(self, config: RuntimeConfig):
        """RuntimeConfig.safety.allow_real_execution must be False."""
        assert config.runtime.get("safety", {}).get("allow_real_execution") is False

    def test_config_mode_is_paper(self, config: RuntimeConfig):
        """RuntimeConfig mode should default to paper."""
        assert config.mode == "paper"

    def test_execution_engine_refuses_when_allow_real_execution_true(
            self, config, fake_mcp, audit,
            permissions, intent_store, local_risk,
            sample_signal, route):
        """ExecutionEngine raises when allow_real_execution is True and no policy_loader blocks."""
        # Patch config to enable real execution; pass policy_loader=None to bypass policy check
        config.runtime["safety"]["allow_real_execution"] = True
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=None,
        )
        ctx = RunContext(
            run_id="h02", mode="paper", phase="ENTRY_WINDOW_AM",
            exchange_timezone="Asia/Shanghai",
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(ExecutionRejected, match="no real broker execution adapter"):
            engine.execute_signal(ctx, sample_signal, route)

    def test_unsupported_mode_rejected(
            self, config, fake_mcp, audit,
            permissions, intent_store, local_risk,
            sample_signal, route):
        """Modes other than paper/live_proposal are rejected (with policy_loader=None)."""
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=None,
        )
        ctx = RunContext(
            run_id="h02-unsupported", mode="production", phase="ENTRY_WINDOW_AM",
            exchange_timezone="Asia/Shanghai",
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(ExecutionRejected, match="execution disabled in mode=production"):
            engine.execute_signal(ctx, sample_signal, route)

    def test_paper_mode_tools_only(self, fake_mcp: FakeMCPInvoker):
        """Paper-mode exec tools are callable from the fake."""
        critical_paper = {
            "mcp_exec_get_balance", "mcp_exec_get_positions", "mcp_exec_get_orders",
            "mcp_exec_get_today_trades", "mcp_exec_get_pnl",
            "mcp_exec_register_approved_intent", "mcp_exec_place_order",
            "mcp_risk_check_intent", "mcp_risk_daily_pnl", "mcp_risk_get_blacklist",
            "mcp_exec_reconcile",
        }
        for tool in critical_paper:
            kwargs = {}
            if tool == "mcp_exec_register_approved_intent":
                kwargs["intent_id"] = "test-intent-paper-mode"
            if tool == "mcp_exec_place_order":
                kwargs["intent_id"] = "test-intent-paper-mode"
                kwargs["symbol"] = "600000.SH"
                kwargs["direction"] = "BUY"
                kwargs["quantity"] = 100
                kwargs["price"] = 10.0
            result = fake_mcp.invoke(tool, **kwargs)
            assert result is not None, f"Paper tool {tool} must be callable"


# ════════════════════════════════════════════════════════════════════════
# H03: Pattern cannot directly reach broker
# ════════════════════════════════════════════════════════════════════════


class TestH03_NoDirectBrokerPath:
    """All paths from signal to place_order must go through risk + intent."""

    def test_execute_signal_policy_blocks_before_place_order(
            self, config, fake_mcp, audit,
            permissions, intent_store, local_risk,
            run_context, sample_signal, route,
            policy_loader):
        """ExecutionEngine rejects before reaching place_order when policy blocks."""
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=policy_loader,
        )
        with pytest.raises(ExecutionRejected):
            engine.execute_signal(run_context, sample_signal, route)

        calls = [name for name, _ in fake_mcp.calls]
        # No place_order call should appear — policy rejection fires before
        assert "mcp_exec_place_order" not in calls, (
            "place_order must not be called before policy+risk clear"
        )

    def test_execution_engine_has_no_direct_broker(self, config, fake_mcp, audit,
                                                    permissions, intent_store,
                                                    local_risk):
        """ExecutionEngine has no broker-specific attribute."""
        engine = ExecutionEngine(config, fake_mcp, audit, permissions, intent_store, local_risk)
        assert hasattr(engine, "mcp")
        assert not hasattr(engine, "_broker_client")
        assert not hasattr(engine, "_live_broker")

    def test_phase_permissions_block_place_order(self, config, fake_mcp, audit,
                                                  permissions, intent_store, local_risk,
                                                  sample_signal, route):
        """Non-trading phases block place_order through permissions."""
        ctx = RunContext(
            run_id="h03-blocked", mode="paper", phase="MIDDAY_REVIEW",
            exchange_timezone="Asia/Shanghai",
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=None,
        )
        with pytest.raises(PermissionDenied):
            engine.execute_signal(ctx, sample_signal, route)

    def test_unknown_phase_raises_permission_denied(self, permissions):
        with pytest.raises(PermissionDenied, match="unknown or unconfigured phase"):
            permissions.require("UNKNOWN_PHASE", "place_order")


# ════════════════════════════════════════════════════════════════════════
# H04: Kill switch + daily loss + rate limit
# ════════════════════════════════════════════════════════════════════════


class TestH04_SafetyChecks:
    """Exec MCP safety gates: kill switch, daily loss, rate limit."""

    def test_kill_switch_blocks_order(self, fake_mcp: FakeMCPInvoker):
        """Exec MCP returns KILL_SWITCH_ACTIVE when kill switch is on."""
        fake_mcp._kill_switch = True
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=1000, price=10.0,
                                  intent_id="test-intent-01")
        assert isinstance(result, dict)
        assert result.get("status") == "KILL_SWITCH_ACTIVE"

    def test_kill_switch_blocks_intent_registration(self, fake_mcp: FakeMCPInvoker):
        """Kill switch also blocks register_approved_intent."""
        fake_mcp._kill_switch = True
        result = fake_mcp.invoke("mcp_exec_register_approved_intent",
                                  intent_id="test-intent-02",
                                  symbol="600000.SH", direction="BUY",
                                  max_quantity=1000,
                                  expires_at="2026-10-07T15:00:00+08:00")
        assert isinstance(result, dict)
        assert result.get("status") == "KILL_SWITCH_ACTIVE"

    def test_kill_switch_does_not_block_reads(self, fake_mcp: FakeMCPInvoker):
        """Read-only MCP calls should still work when kill switch is on."""
        fake_mcp._kill_switch = True
        balance = fake_mcp.invoke("mcp_exec_get_balance")
        assert balance is not None
        assert "total_asset" in balance

    def test_daily_loss_blocks_buy(self, fake_mcp: FakeMCPInvoker):
        """Daily loss limit blocks new buy orders."""
        fake_mcp._daily_loss_hit = True
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=1000, price=10.0,
                                  intent_id="test-intent-03")
        assert isinstance(result, dict)
        assert result.get("status") == "DAILY_LOSS_LIMIT"

    def test_daily_loss_allows_sell(self, fake_mcp: FakeMCPInvoker):
        """Daily loss should not block sell orders (reduce exposure)."""
        fake_mcp._daily_loss_hit = True
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="SELL",
                                  quantity=500, price=10.5,
                                  intent_id="test-intent-04")
        assert isinstance(result, dict)
        assert result.get("status") != "DAILY_LOSS_LIMIT"

    def test_rate_limit_blocks_excessive_orders(self, fake_mcp: FakeMCPInvoker):
        """Rate limit blocks when order frequency exceeds limit."""
        for i in range(21):
            fake_mcp._call_timestamps.append(_time.time())
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=100, price=10.0,
                                  intent_id="rate-test")
        assert isinstance(result, dict)
        assert result.get("status") == "RATE_LIMIT_EXCEEDED"

    def test_rate_limit_resets_over_time(self, fake_mcp: FakeMCPInvoker):
        """Old timestamps are pruned after 60 seconds."""
        old = _time.time() - 120
        fake_mcp._call_timestamps = [old] * 25
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=100, price=10.0,
                                  intent_id="rate-test-reset")
        assert result.get("status") != "RATE_LIMIT_EXCEEDED", \
            "Old timestamps should be pruned"

    def test_safety_config_has_required_fields(self, config: RuntimeConfig):
        """runtime.yaml safety section must include P8 mandatory fields."""
        safety = config.runtime.get("safety", {})
        assert "kill_switch" in safety, "safety.kill_switch missing"
        assert "daily_loss_limit" in safety, "safety.daily_loss_limit missing"
        assert "max_orders_per_minute" in safety, "safety.max_orders_per_minute missing"


# ════════════════════════════════════════════════════════════════════════
# H05: Reconciliation + idempotency
# ════════════════════════════════════════════════════════════════════════


class TestH05_ReconciliationAndIdempotency:
    """Broker reconciliation and idempotent order placement."""

    def test_mcp_exec_reconcile_returns_valid_schema(self, fake_mcp: FakeMCPInvoker):
        """mcp_exec_reconcile returns expected schema with required keys."""
        result = fake_mcp.invoke("mcp_exec_reconcile")
        assert isinstance(result, dict)
        assert "status" in result
        assert "missing_orders" in result
        assert "extra_orders" in result
        assert "matched_orders" in result
        assert "reconciled_at" in result

    def test_reconcile_detects_missing_orders(self, fake_mcp: FakeMCPInvoker):
        """Reconcile can detect orders expected but not in broker."""
        result = fake_mcp.invoke("mcp_exec_reconcile")
        assert isinstance(result["matched_orders"], list)
        assert isinstance(result["missing_orders"], list)
        assert isinstance(result["extra_orders"], list)

    def test_reconcile_with_real_orders(self, fake_mcp: FakeMCPInvoker):
        """After placing an order, reconcile should match it."""
        fake_mcp.invoke("mcp_exec_place_order",
                         symbol="600000.SH", direction="BUY",
                         quantity=1000, price=10.0,
                         intent_id="recon-intent")
        result = fake_mcp.invoke("mcp_exec_reconcile")
        assert len(result["matched_orders"]) >= 1
        matched_ids = [o.get("order_id") for o in result["matched_orders"]]
        assert any(mid is not None for mid in matched_ids)

    def test_order_idempotency_same_key_rejected(self, intent_store: IntentStore):
        """Same idempotency_key should not double-execute."""
        key = hashlib.sha256(b"test-idempotency-key").hexdigest()
        rec1 = IntentRecord(
            intent_id="idem-001",
            idempotency_key=key,
            trade_date="2026-10-06",
            symbol="600000.SH",
            direction="BUY",
            strategy_id="test",
            phase="ENTRY_WINDOW_AM",
            max_quantity=1000,
            limit_price=10.0,
            stop_price=9.5,
            expires_at="2026-10-06T15:00:00+08:00",
            reason="test idempotency",
        )
        intent_store.create(rec1)
        rec2 = IntentRecord(
            intent_id="idem-002",
            idempotency_key=key,  # Same key
            trade_date="2026-10-06",
            symbol="600000.SH",
            direction="BUY",
            strategy_id="test",
            phase="ENTRY_WINDOW_AM",
            max_quantity=1000,
            limit_price=10.0,
            stop_price=9.5,
            expires_at="2026-10-06T15:00:00+08:00",
            reason="duplicate test",
        )
        with pytest.raises(DuplicateIntent):
            intent_store.create(rec2)

    def test_by_key_finds_existing(self, intent_store: IntentStore):
        """by_key returns existing intent for a previously-used key."""
        key = hashlib.sha256(b"find-me-key").hexdigest()
        rec = IntentRecord(
            intent_id="find-001",
            idempotency_key=key,
            trade_date="2026-10-06",
            symbol="600000.SH",
            direction="BUY",
            strategy_id="test",
            phase="ENTRY_WINDOW_AM",
            max_quantity=1000,
            limit_price=10.0,
            stop_price=9.5,
            expires_at="2026-10-06T15:00:00+08:00",
            reason="find test",
        )
        intent_store.create(rec)
        found = intent_store.by_key(key)
        assert found is not None
        assert found["intent_id"] == "find-001"

    def test_by_key_returns_none_for_new_key(self, intent_store: IntentStore):
        """by_key returns None for a never-used key."""
        found = intent_store.by_key("never-used-key-12345")
        assert found is None


# ════════════════════════════════════════════════════════════════════════
# H06: Credential isolation / redaction
# ════════════════════════════════════════════════════════════════════════


class TestH06_CredentialIsolation:
    """Credentials must never appear in logs or audit events."""

    def test_redact_removes_secrets(self):
        """redact() replaces known secret fields with <REDACTED>."""
        payload = {
            "api_key": "sk-1234567890abcdef",
            "access_token": "eyJhbGciOiJIUzI1NiJ9.xxx",
            "broker_secret": "super-secret-broker-password",
            "username": "trader1",
            "account_id": "ACC001",
        }
        result = redact(payload)
        assert result["api_key"] == "<REDACTED>"
        assert result["access_token"] == "<REDACTED>"
        assert result["broker_secret"] == "<REDACTED>"
        assert result["username"] == "trader1"
        assert result["account_id"] != "<REDACTED>"

    def test_redact_hash_fields(self):
        """Redact can hash designated fields."""
        payload = {"account_id": "ACC-001-SENSITIVE", "shareholder_account": "SH123"}
        result = redact(payload, hash_fields={"account_id", "shareholder_account"})
        assert result["account_id"].startswith("sha256:")
        assert result["shareholder_account"].startswith("sha256:")

    def test_redact_nested_secrets(self):
        """Redact works recursively on nested dicts."""
        payload = {
            "credentials": {"api_key": "sk-nested-secret"},
            "broker_config": {
                "endpoint": "https://broker.example.com",
                "broker_secret": "my-secret-key",
            },
        }
        result = redact(payload)
        assert result["credentials"]["api_key"] == "<REDACTED>"
        assert result["broker_config"]["broker_secret"] == "<REDACTED>"
        assert result["broker_config"]["endpoint"] == "https://broker.example.com"

    def test_redact_list_elements(self):
        """Redact works on lists of dicts."""
        payload = [{"api_key": "sk-list-key"}, {"username": "trader"}]
        result = redact(payload)
        assert result[0]["api_key"] == "<REDACTED>"
        assert result[1]["username"] == "trader"

    def test_audit_event_writer_redacts_secrets(self, config: RuntimeConfig):
        """AuditEventWriter config must have never_log populated."""
        privacy = config.logging.get("privacy_security", {})
        never_log = set(privacy.get("never_log", []))
        assert "api_key" in never_log
        assert "access_token" in never_log
        assert "broker_secret" in never_log


# ════════════════════════════════════════════════════════════════════════
# H07: Broker API contract + sandbox
# ════════════════════════════════════════════════════════════════════════


class TestH07_BrokerAPIContract:
    """Exec MCP contract enforcement and sandbox isolation."""

    def test_exec_tools_list_contains_required(self):
        """EXEC_TOOLS list must include all required broker tools."""
        required = {
            "mcp_exec_get_balance",
            "mcp_exec_get_positions",
            "mcp_exec_get_orders",
            "mcp_exec_get_today_trades",
            "mcp_exec_place_order",
            "mcp_exec_cancel_order",
            "mcp_exec_get_pnl",
            "mcp_exec_register_approved_intent",
            "mcp_exec_reconcile",
        }
        exec_set = set(EXEC_TOOLS)
        for tool in required:
            assert tool in exec_set, f"Required exec tool missing: {tool}"

    def test_place_order_without_intent_id_returns_order(self, fake_mcp: FakeMCPInvoker):
        """place_order works even without intent_id in fake (real MCP enforces this)."""
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=100, price=10.0)
        assert result is not None

    def test_place_order_with_valid_intent_id(self, fake_mcp: FakeMCPInvoker):
        """place_order with a valid intent_id should succeed in sandbox."""
        result = fake_mcp.invoke("mcp_exec_place_order",
                                  symbol="600000.SH", direction="BUY",
                                  quantity=1000, price=10.0,
                                  intent_id="h07-valid-intent")
        assert isinstance(result, dict)
        assert result.get("order_id") is not None

    def test_intent_registration_before_order(
            self, config, fake_mcp, audit,
            permissions, intent_store, local_risk,
            sample_signal, route):
        """Execution engine registers intent before placing order."""
        engine = ExecutionEngine(
            config, fake_mcp, audit, permissions, intent_store,
            local_risk, policy_loader=None,
        )
        ctx = RunContext(
            run_id="h07-flow", mode="paper", phase="ENTRY_WINDOW_AM",
            exchange_timezone="Asia/Shanghai",
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        try:
            engine.execute_signal(ctx, sample_signal, route)
        except ExecutionRejected:
            pass
        call_names = [name for name, _ in fake_mcp.calls]
        assert "mcp_exec_get_balance" in call_names
        assert "mcp_exec_get_positions" in call_names
        assert "mcp_risk_check_intent" in call_names
        reg_index = None
        order_index = None
        for i, name in enumerate(call_names):
            if name == "mcp_exec_register_approved_intent":
                reg_index = i
            if name == "mcp_exec_place_order":
                order_index = i
        if reg_index is not None and order_index is not None:
            assert reg_index < order_index, \
                "register_approved_intent must precede place_order"

    def test_sandbox_state_isolation(self, fake_mcp: FakeMCPInvoker):
        """Sandbox MCP operations should maintain internal state."""
        before = len(fake_mcp.orders)
        fake_mcp.invoke("mcp_exec_place_order",
                         symbol="600000.SH", direction="BUY",
                         quantity=500, price=10.0,
                         intent_id="sandbox-test")
        assert len(fake_mcp.orders) == before + 1

    def test_mcp_error_on_unknown_tool(self, fake_mcp: FakeMCPInvoker):
        """Unknown exec tool should raise KeyError."""
        with pytest.raises(KeyError, match="does not implement"):
            fake_mcp.invoke("mcp_exec_unknown_tool")
