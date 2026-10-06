from __future__ import annotations

import uuid
import logging
from typing import Any

logger = logging.getLogger(__name__)

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
from ..strategy.candidates import build_engine
from ..strategy.decision_engine import DecisionEngine
from ..strategy.decision_state import DecisionStatus
from ..execution.paper_engine import PaperEngine
from ..execution.paper_ledger import PaperLedger
from ..risk.paper_risk import PaperRiskEngine


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

        # P4 Candidate scanning engine
        self.candidate_engine = build_engine()
        self.last_candidates: dict[str, Any] = {"scanner_result": None, "decisions": []}
        # P5 Decision engines — one per symbol
        self._decision_pipeline = DecisionEngine()
        # P6 Paper engine — lazy init
        self._paper_engine: PaperEngine | None = None

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

    
    def view_candidates(self) -> dict[str, Any]:
        """Expose the latest candidate scan results."""
        return dict(self.last_candidates)

    def _get_paper_engine(self) -> PaperEngine:
        if self._paper_engine is None:
            ledger_path = self.config.project_root / "data" / "audit" / "paper_ledger.json"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger = PaperLedger(persist_path=ledger_path)
            risk = PaperRiskEngine(initial_cash=1_000_000.0)
            self._paper_engine = PaperEngine(
                ledger=ledger,
                risk=risk,
                initial_cash=1_000_000.0,
                allow_real_execution=False,
            )
        return self._paper_engine

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
        # ── P4: Candidate scan ────────────────────────────────────────────
        scan_result_data: dict[str, Any] | None = None
        decisions_data: list[dict[str, Any]] = []
        try:
            enabled_patterns: list[tuple[str, str]] = []
            if self.policy_loader is not None:
                try:
                    active_policies = self.policy_loader.load_active()
                    for entry in active_policies.values():
                        status = str(entry.status.value).lower() if hasattr(entry.status, 'value') else str(entry.status).lower()
                        if status in ("approved", "pending") and entry.is_valid:
                            enabled_patterns.append((entry.key[2], entry.key[3]))
                except Exception:
                    logger.exception("Failed to load active policies for candidate scan")
            # Fetch bars for monitored symbols (positions + market symbols)
            scan_symbols: list[str] = []
            positions_raw = account.get("positions", [])
            if isinstance(positions_raw, list):
                for p in positions_raw:
                    sym = p.get("symbol", "") if isinstance(p, dict) else ""
                    if sym:
                        scan_symbols.append(sym)
            market_syms = market.get("symbols", market.get("universe", []))
            if isinstance(market_syms, list):
                scan_symbols = list(dict.fromkeys(scan_symbols + market_syms))
            bars_by_symbol: dict[str, list[dict]] = {}
            for sym in scan_symbols:
                try:
                    kline = self.mcp.invoke("mcp_intel_tdx_kline", symbol=sym, period="D", count=120)
                    bars = kline if isinstance(kline, list) else (kline.get("bars", []) if isinstance(kline, dict) else [])
                    if bars:
                        bars_by_symbol[sym] = bars
                except Exception:
                    logger.warning("Failed fetching bars for %s", sym, exc_info=True)
            if enabled_patterns and bars_by_symbol:
                # P4 + P5: Single pipeline codepath
                decisions = self._decision_pipeline.pipeline(
                    bars_by_symbol=bars_by_symbol,
                    context=market,
                    enabled_patterns=enabled_patterns,
                    candidate_engine=self.candidate_engine,
                    policy_loader=self.policy_loader,
                    phase=run.phase,
                )
                decisions_data = [d.to_dict() for d in decisions]
                scan_result_data = self._decision_pipeline._last_scanner_result.to_dict() if self._decision_pipeline._last_scanner_result else None
                # ── P6: Paper engine execution ────────────────────────
                for sd in decisions:
                    decision_dict = sd.to_dict()
                    if sd.status in (DecisionStatus.BUY_TRIGGERED, DecisionStatus.SELL_TRIGGERED):
                        try:
                            paper = self._get_paper_engine()
                            paper_result = paper.process_signal(decision_dict)
                            event_type = "PAPER_BUY_ORDER" if sd.status == DecisionStatus.BUY_TRIGGERED else "PAPER_SELL_ORDER"
                            self.audit.write_event(
                                event_type=event_type, phase=run.phase,
                                run_id=run.run_id, trace_id=trace,
                                producer={"type": "exec", "id": "paper-engine"},
                                symbol=sd.symbol,
                                payload=paper_result,
                            )
                        except Exception:
                            logger.exception("Paper engine execution failed for %s", sd.symbol)
                # ── Audit candidate scan ──────────────────────────────────
                sr = self._decision_pipeline._last_scanner_result
                if sr is not None:
                    self.audit.write_event(
                        event_type="CANDIDATE_SCAN", phase=run.phase, run_id=run.run_id, trace_id=trace,
                        producer={"type": "strategy", "id": "candidate-engine"},
                        payload={
                            "top_n_count": len(sr.top_n),
                            "total_scanned": sr.total_scanned,
                            "skipped_liquidity": sr.skipped_liquidity,
                            "skipped_rs": sr.skipped_rs,
                            "skipped_sector": sr.skipped_sector,
                            "llm_vetoed": sr.llm_vetoed,
                            "as_of": sr.as_of,
                            "decision_count": len(decisions_data),
                        },
                    )
        except Exception:
            logger.exception("Candidate scan/decision cycle failed during intraday monitor")
        self.last_candidates["scanner_result"] = scan_result_data
        self.last_candidates["decisions"] = decisions_data
        return {"market": market, "account": account, "candidates": scan_result_data, "decisions": decisions_data}

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
