from __future__ import annotations

import uuid
from typing import Any

from ..audit.event_writer import AuditEventWriter
from ..audit.manifest import DailyManifestBuilder
from ..audit.replay import ReplayEngine
from ..config import RuntimeConfig
from ..market.context import MarketContextBuilder
from ..mcp.base import MCPInvoker
from ..models import RunContext
from ..review.daily import DailyReviewer
from ..strategy.signal_engine import DeterministicSignalEngine
from .recovery import RecoveryManager
from ..strategy.router import StrategyRouter
from ..strategy.policy_loader import PolicyLoader
from ..utils import as_trade_date, extract_symbols
from .schema_validator import SchemaRegistry


class AgentOrchestrator:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker, audit: AuditEventWriter,
                 replay: ReplayEngine, reviewer: DailyReviewer, manifest: DailyManifestBuilder, recovery: RecoveryManager | None = None, *, policy_loader=None):
        self.config, self.mcp, self.audit = config, mcp, audit
        self.replay, self.reviewer, self.manifest = replay, reviewer, manifest
        self.market = MarketContextBuilder(config, mcp)
        self.policy_loader = policy_loader if policy_loader is not None else PolicyLoader.from_runtime_config(config)
        self.router = StrategyRouter(config.strategy_router, policy_loader=self.policy_loader, audit=audit)
        self.signal_engine = DeterministicSignalEngine()
        self.recovery_manager = recovery
        self.schemas = SchemaRegistry(config.project_root / "skill" / "schemas")

    def run_phase(self, run: RunContext, **kwargs: Any) -> dict[str, Any]:
        handlers = {
            "PREOPEN_PRECHECK": self.precheck,
            "RECOVERY_SYNC": self.recovery_sync,
            "PREOPEN_CONTEXT": self.preopen_context,
            "OPEN_AUCTION_OBSERVE": self.intraday_monitor,
            "OPEN_AUCTION_FREEZE": self.intraday_monitor,
            "OPEN_RECONCILE": self.intraday_monitor,
            "OPEN_STABILIZE": self.intraday_monitor,
            "ENTRY_WINDOW_AM": self.intraday_monitor,
            "MORNING_MONITOR": self.intraday_monitor,
            "MORNING_CLOSE_PROTECT": self.intraday_monitor,
            "PM_PRECHECK": self.intraday_monitor,
            "PM_STABILIZE": self.intraday_monitor,
            "ENTRY_WINDOW_PM": self.intraday_monitor,
            "LATE_SESSION_MANAGE": self.intraday_monitor,
            "CLOSE_AUCTION_FREEZE": self.intraday_monitor,
            "EOD_UNIVERSE_SCAN": self.eod_universe_scan,
            "EOD_DEEP_DIVE": self.eod_deep_dive,
            "NIGHT_EVENT_CHECK": self.night_event_check,
            "DAILY_REVIEW": self.daily_review,
            "AUDIT_FINALIZE": self.audit_finalize,
            "CLOSE_RECONCILE": self.close_reconcile,
            "MIDDAY_REVIEW": self.close_reconcile,
        }
        handler = handlers.get(run.phase)
        if not handler:
            return self.audit_only_phase(run, **kwargs)
        return handler(run, **kwargs)

    def _trace(self) -> str: return str(uuid.uuid4())

    def precheck(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace = self._trace()
        data = {
            "calendar": self.mcp.invoke("mcp_intel_is_trading_day"),
            "sessions": self.mcp.invoke("mcp_intel_trading_sessions"),
            "tdx_health": self.mcp.invoke("mcp_intel_tdx_health"),
            "balance": self.mcp.invoke("mcp_exec_get_balance"),
            "positions": self.mcp.invoke("mcp_exec_get_positions"),
            "orders": self.mcp.invoke("mcp_exec_get_orders", status="PENDING"),
            "today_trades": self.mcp.invoke("mcp_exec_get_today_trades"),
            "risk_daily_pnl": self.mcp.invoke("mcp_risk_daily_pnl"),
            "blacklist": self.mcp.invoke("mcp_risk_get_blacklist"),
        }
        tradable = bool((data["calendar"] or {}).get("trading_day", True)) and bool((data["tdx_health"] or {}).get("ok", True))
        result = {"tradable": tradable, "data": data}
        self.audit.write_event(event_type="PRECHECK_RESULT", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"scheduler","id":"orchestrator"}, status="ok" if tradable else "warning", payload=result)
        return result

    def recovery_sync(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace = self._trace()
        if self.recovery_manager is not None:
            result = self.recovery_manager.sync()
        else:
            result = self.precheck(run)
            result = {"state": "SYNCED" if result.get("tradable") else "DEGRADED_NO_ORDER", "precheck": result, "unresolved": []}
        self.audit.write_event(event_type="RECOVERY_SYNC", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"scheduler","id":"recovery-manager"},
                               status="ok" if result.get("state") == "SYNCED" else "warning", payload=result)
        return result

    def preopen_context(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace = self._trace(); ctx = self.market.build(); self.schemas.validate("market_context.schema.json", ctx)
        snap = self.audit.snapshot_store.put(as_trade_date(), ctx)
        self.audit.write_event(event_type="MARKET_CONTEXT", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"intel","id":"market-context-builder"}, payload=ctx,
                               source_refs=[snap["snapshot_ref"]])
        return ctx

    def intraday_monitor(self, run: RunContext, **_: Any) -> dict[str, Any]:
        """Refresh market/account state during active windows without autonomously inventing trades.

        LLM/strategy execution remains a separate decision path. This phase gives the
        background worker deterministic, auditable work to perform while markets are open.
        """
        trace = self._trace()
        market = self.market.build()
        self.schemas.validate("market_context.schema.json", market)
        market_snap = self.audit.snapshot_store.put(as_trade_date(), market)
        self.audit.write_event(
            event_type="MARKET_CONTEXT_REFRESH", phase=run.phase, run_id=run.run_id, trace_id=trace,
            producer={"type":"intel","id":"intraday-market-monitor"}, payload=market,
            source_refs=[market_snap["snapshot_ref"]],
        )
        account = {
            "balance": self.mcp.invoke("mcp_exec_get_balance"),
            "positions": self.mcp.invoke("mcp_exec_get_positions"),
            "pending_orders": self.mcp.invoke("mcp_exec_get_orders", status="PENDING"),
            "today_trades": self.mcp.invoke("mcp_exec_get_today_trades"),
            "risk_pnl": self.mcp.invoke("mcp_risk_daily_pnl"),
        }
        self.audit.write_event(
            event_type="ACCOUNT_SNAPSHOT", phase=run.phase, run_id=run.run_id, trace_id=trace,
            producer={"type":"risk","id":"intraday-account-monitor"}, payload=account,
        )
        return {"market": market, "account": account}

    def night_event_check(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace = self._trace()
        payload: dict[str, Any] = {}
        try:
            payload["calendar"] = self.mcp.invoke("mcp_jin10_list_calendar")
        except Exception as exc:
            payload["calendar_error"] = str(exc)
        try:
            payload["hot_signals"] = self.mcp.invoke("mcp_intel_fetch_hot_signals")
        except Exception as exc:
            payload["hot_signals_error"] = str(exc)
        status = "warning" if any(k.endswith("_error") for k in payload) else "ok"
        self.audit.write_event(
            event_type="NIGHT_EVENT_CHECK", phase=run.phase, run_id=run.run_id, trace_id=trace,
            producer={"type":"intel","id":"night-event-monitor"}, status=status, payload=payload,
        )
        return payload

    def eod_universe_scan(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace = self._trace(); uq = self.config.runtime.get("universe_queries", {}); limit=int(uq.get("per_source_limit",200))
        raw = []
        for q in uq.get("wencai", []): raw.append(self.mcp.invoke("mcp_intel_wencai_search", query=q, page=1, limit=limit))
        for q in uq.get("tdx", []): raw.append(self.mcp.invoke("mcp_intel_tdx_screener", query=q, limit=limit))
        for q in uq.get("generic", []): raw.append(self.mcp.invoke("mcp_intel_screen_stocks", query=q, limit=limit))
        symbols = list(dict.fromkeys(s for x in raw for s in extract_symbols(x)))
        batch = self.mcp.invoke("mcp_intel_query_batch_data", symbols=symbols) if symbols else []
        payload = {"universe_count": len(symbols), "hard_filter_pass": len(symbols), "symbols": symbols, "batch": batch}
        self.audit.write_event(event_type="CANDIDATE_BATCH", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"intel","id":"universe-engine"}, payload=payload)
        return payload

    def eod_deep_dive(self, run: RunContext, symbols: list[str] | None = None, **_: Any) -> dict[str, Any]:
        if symbols is None:
            row = self.audit.index.latest(as_trade_date(), "CANDIDATE_BATCH")
            if not row: return {"reports": [], "reason": "no candidate batch"}
            payload = __import__('json').loads(row.get("payload_json") or "{}")
            symbols = payload.get("symbols", [])
        cap = int(self.config.schedule.get("candidate_flow", {}).get("eod_deep_dive_cap", 200))
        reports=[]
        for symbol in symbols[:cap]:
            trace=self._trace()
            kline = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=260)
            bars = kline if isinstance(kline, list) else (kline.get("bars", []) if isinstance(kline, dict) else [])
            signals = self.signal_engine.scan(bars)
            policy_decisions = []
            if self.policy_loader is not None:
                # This descriptive scanner has no authenticated expanded Context yet.
                # Unknown context cannot promote detections to entry authorization.
                signals = self.policy_loader.filter_signals(signals, market_context={}, sector_context={}, decisions=policy_decisions)
                for decision in policy_decisions:
                    self.audit.write_event(event_type="POLICY_DECISION", phase=run.phase, run_id=run.run_id,
                        trace_id=trace, producer={"type": "intel", "id": "deep-dive-engine"}, symbol=symbol,
                        status="ok" if decision["allowed"] else "reject",
                        payload={"boundary": "descriptive_scanner", "policy_decision": decision})
            report={
                "symbol": symbol,
                "kline": kline,
                "deterministic_signals": signals,
                "technical": self.mcp.invoke("mcp_intel_get_technical_indicators", symbol=symbol),
                "chip": self.mcp.invoke("mcp_intel_get_chip_distribution", symbol=symbol),
                "fund_flow": self.mcp.invoke("mcp_intel_get_fund_flow", symbol=symbol),
                "f10": self.mcp.invoke("mcp_intel_tdx_f10", symbol=symbol, module="basic"),
                "financial": self.mcp.invoke("mcp_intel_get_financial_report", symbol=symbol, num=2),
                "news": self.mcp.invoke("mcp_intel_tdx_news", symbol=symbol),
            }
            if self.policy_loader is not None:
                report["policy_decisions"] = policy_decisions
            snap=self.audit.snapshot_store.put(as_trade_date(), report)
            self.audit.write_event(event_type="DEEP_DIVE_REPORT", phase=run.phase, run_id=run.run_id, trace_id=trace,
                                   producer={"type":"intel","id":"deep-dive-engine"}, symbol=symbol, payload=report,
                                   source_refs=[snap["snapshot_ref"]])
            reports.append({"symbol":symbol,"snapshot_ref":snap["snapshot_ref"]})
        return {"reports":reports}

    def close_reconcile(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace=self._trace(); payload={
            "orders": self.mcp.invoke("mcp_exec_get_orders", status=None),
            "trades": self.mcp.invoke("mcp_exec_get_today_trades"),
            "balance": self.mcp.invoke("mcp_exec_get_balance"),
            "positions": self.mcp.invoke("mcp_exec_get_positions"),
            "pnl": self.mcp.invoke("mcp_exec_get_pnl", period="today"),
            "risk_pnl": self.mcp.invoke("mcp_risk_daily_pnl"),
        }
        self.audit.write_event(event_type="POSITION_SNAPSHOT", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"exec","id":"reconcile"}, payload=payload)
        self.audit.write_event(event_type="PNL_SNAPSHOT", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"exec","id":"reconcile"}, payload=payload["pnl"])
        return payload

    def daily_review(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace=self._trace(); report=self.reviewer.build(as_trade_date())
        self.audit.write_event(event_type="DAILY_REVIEW", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"review","id":"deterministic-reviewer"}, payload=report)
        return report

    def audit_finalize(self, run: RunContext, **_: Any) -> dict[str, Any]:
        trace=self._trace(); verify=self.replay.verify_hash_chain(as_trade_date())
        if not verify.get("ok"):
            self.audit.write_event(event_type="AUDIT_GAP", phase=run.phase, run_id=run.run_id, trace_id=trace,
                                   producer={"type":"scheduler","id":"audit-finalizer"}, status="error", error_code="HASH_CHAIN_INVALID", payload=verify)
        manifest=self.manifest.build(as_trade_date())
        self.audit.write_event(event_type="AUDIT_MANIFEST", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"scheduler","id":"audit-finalizer"}, payload=manifest)
        return {"verify":verify,"manifest":manifest}

    def audit_only_phase(self, run: RunContext, **kwargs: Any) -> dict[str, Any]:
        trace=self._trace(); payload={"phase":run.phase,"note":"No autonomous business handler in v0.1; phase recorded for external LLM/strategy callback.","kwargs":kwargs}
        self.audit.write_event(event_type="PHASE_TICK", phase=run.phase, run_id=run.run_id, trace_id=trace,
                               producer={"type":"scheduler","id":"orchestrator"}, status="skipped", payload=payload)
        return payload
