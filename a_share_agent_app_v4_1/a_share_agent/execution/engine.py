from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from ..audit.event_writer import AuditEventWriter
from ..config import RuntimeConfig
from ..core.permissions import PhasePermissions
from ..mcp.base import MCPInvoker
from ..models import IntentRecord, RunContext
from ..risk.engine import LocalRiskEngine
from ..utils import as_trade_date, now_shanghai, stable_hash
from .intent_store import DuplicateIntent, IntentStore


class ExecutionRejected(RuntimeError):
    pass


class ExecutionEngine:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker, audit: AuditEventWriter,
                 permissions: PhasePermissions, intents: IntentStore, risk: LocalRiskEngine):
        self.config, self.mcp, self.audit = config, mcp, audit
        self.permissions, self.intents, self.risk = permissions, intents, risk

    def execute_signal(self, run: RunContext, signal: dict[str, Any], route: dict[str, Any], *, sector: str | None = None) -> dict[str, Any]:
        symbol = signal["symbol"]; direction = str(signal.get("direction", "BUY")).upper()
        trace_id = signal.get("trace_id") or str(uuid.uuid4())
        strategy_id = signal.get("strategy_id", "unknown_strategy")
        trade_date = as_trade_date()
        is_new_entry = direction == "BUY"
        self.permissions.require(run.phase, "place_order", direction=direction)
        if is_new_entry:
            self.permissions.require(run.phase, "new_entry")
        mode = run.mode
        if mode not in {"paper", "live_proposal"}:
            raise ExecutionRejected(f"execution disabled in mode={mode}")
        if self.config.runtime.get("safety", {}).get("allow_real_execution", False):
            raise ExecutionRejected("this runtime release intentionally has no real broker execution adapter")

        entry = float(signal.get("limit_price") or (signal.get("entry_zone") or {}).get("max") or 0)
        stop = signal.get("stop")
        requested = int(signal.get("quantity") or signal.get("max_quantity") or 0)
        if entry <= 0 or requested <= 0:
            raise ExecutionRejected("signal must include positive limit_price/entry_zone.max and quantity")
        key = signal.get("idempotency_key") or stable_hash([trade_date, strategy_id, symbol, direction, run.phase, signal.get("signal_version", "v1")])
        if self.intents.by_key(key):
            raise ExecutionRejected("duplicate idempotency key")
        if is_new_entry and self.config.defaults.get("trade_behavior", {}).get("one_entry_per_symbol_per_day", True) and self.intents.has_entry_today(trade_date, symbol):
            raise ExecutionRejected("one_entry_per_symbol_per_day")

        balance = self.mcp.invoke("mcp_exec_get_balance")
        positions = self.mcp.invoke("mcp_exec_get_positions")
        orders = self.mcp.invoke("mcp_exec_get_orders", status="PENDING")
        trades = self.mcp.invoke("mcp_exec_get_today_trades")
        blacklist = self.mcp.invoke("mcp_risk_get_blacklist")
        if is_new_entry and any(str(o.get("symbol")) == symbol and str(o.get("direction", "")).upper() == "BUY" for o in (orders or [])):
            raise ExecutionRejected("pending buy exists")
        if is_new_entry and any(str(t.get("symbol")) == symbol and str(t.get("direction", "")).upper() == "BUY" for t in (trades or [])):
            raise ExecutionRejected("buy already traded today")

        local = self.risk.assess(direction=direction, symbol=symbol, requested_quantity=requested, entry_price=entry,
                                 stop_price=float(stop) if stop is not None else None, balance=balance,
                                 positions=positions or [], blacklist=blacklist,
                                 route_multiplier=float(route.get("position_multiplier", 1.0)), sector=sector)
        self.audit.write_event(event_type="RISK_INTENT", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"scheduler","id":"execution-engine"}, symbol=symbol, strategy_id=strategy_id,
                               payload={"signal": signal, "route": route, "local_risk": local.to_dict()})
        if local.status != "PASS":
            self.audit.write_event(event_type="RISK_RESULT", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                                   producer={"type":"risk","id":"local-risk"}, status="reject", symbol=symbol,
                                   strategy_id=strategy_id, payload={"status":"REJECT","local":local.to_dict()})
            raise ExecutionRejected("local risk rejected: " + ",".join(local.reason_codes))

        quote = self.mcp.invoke("mcp_intel_query_multi_source_quote", symbol=symbol, tolerance_pct=0.2)
        if not self._quote_ok(quote):
            raise ExecutionRejected("multi-source quote conflict")
        consensus = self._consensus(quote) or entry
        remote_payload = {"symbol": symbol, "direction": direction, "quantity": local.approved_quantity,
                          "price": min(entry, consensus) if direction == "BUY" else max(entry, consensus),
                          "stop": stop, "strategy_id": strategy_id, "phase": run.phase}
        remote = self.mcp.invoke("mcp_risk_check_intent", intent=remote_payload)
        remote_status = str(remote.get("status", remote.get("decision", ""))).upper() if isinstance(remote, dict) else ""
        self.audit.write_event(event_type="RISK_RESULT", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"risk","id":"risk-mcp"}, status="ok" if remote_status=="PASS" else "reject",
                               symbol=symbol, strategy_id=strategy_id, payload={"status": remote_status or "UNKNOWN", "remote": remote, "local": local.to_dict()})
        if remote_status != "PASS":
            raise ExecutionRejected(f"risk MCP did not PASS: {remote_status or 'UNKNOWN'}")

        intent_id = signal.get("intent_id") or f"INT-{trade_date.replace('-','')}-{uuid.uuid4().hex[:12]}"
        rec = IntentRecord(intent_id=intent_id, idempotency_key=key, trade_date=trade_date, symbol=symbol,
                           direction=direction, strategy_id=strategy_id, phase=run.phase,
                           max_quantity=local.approved_quantity, limit_price=remote_payload["price"],
                           stop_price=float(stop) if stop is not None else None,
                           expires_at=(now_shanghai()+timedelta(minutes=15)).isoformat(),
                           reason=str(signal.get("reason", signal.get("decision", "signal"))),
                           metadata={"trace_id": trace_id, "quote": quote, "route_id": route.get("route_id")})
        try: self.intents.create(rec)
        except DuplicateIntent as exc: raise ExecutionRejected("duplicate intent") from exc
        self.audit.write_event(event_type="INTENT_CREATED", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"scheduler","id":"execution-engine"}, symbol=symbol, strategy_id=strategy_id,
                               intent_id=intent_id, payload=rec.to_dict())

        if mode == "live_proposal":
            self.intents.set_status(intent_id, "RISK_APPROVED")
            proposal = {"status":"ORDER_PROPOSAL", "intent_id":intent_id, "symbol":symbol, "direction":direction,
                        "quantity":local.approved_quantity, "price":remote_payload["price"], "human_confirmation_required":True}
            self.audit.write_event(event_type="ORDER_PROPOSAL", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                                   producer={"type":"scheduler","id":"execution-engine"}, symbol=symbol, strategy_id=strategy_id,
                                   intent_id=intent_id, payload=proposal)
            return proposal

        reg = self.mcp.invoke("mcp_exec_register_approved_intent", intent_id=intent_id, symbol=symbol, direction=direction,
                              max_quantity=local.approved_quantity, expires_at=rec.expires_at)
        self.intents.set_status(intent_id, "REGISTERED", {"register_receipt": reg})
        self.audit.write_event(event_type="INTENT_REGISTERED", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"exec","id":"exec-mcp"}, symbol=symbol, strategy_id=strategy_id,
                               intent_id=intent_id, payload=reg)
        order_req = {"symbol":symbol,"direction":direction,"quantity":local.approved_quantity,"price":remote_payload["price"],"intent_id":intent_id,"reason":rec.reason}
        self.audit.write_event(event_type="ORDER_REQUEST", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"scheduler","id":"execution-engine"}, symbol=symbol, strategy_id=strategy_id,
                               intent_id=intent_id, payload=order_req)
        receipt = self.mcp.invoke("mcp_exec_place_order", **order_req)
        self.intents.set_status(intent_id, "SUBMITTED", {"order_receipt": receipt})
        self.audit.write_event(event_type="EXECUTION_RECEIPT", phase=run.phase, run_id=run.run_id, trace_id=trace_id,
                               producer={"type":"exec","id":"exec-mcp"}, symbol=symbol, strategy_id=strategy_id,
                               intent_id=intent_id, payload=receipt)
        return {"intent": rec.to_dict(), "receipt": receipt, "local_risk": local.to_dict(), "remote_risk": remote, "quote": quote}

    @staticmethod
    def _quote_ok(q: Any) -> bool:
        if not isinstance(q, dict): return False
        if q.get("ok") is False: return False
        dev = q.get("max_deviation_pct")
        return dev is None or float(dev) <= .2

    @staticmethod
    def _consensus(q: Any) -> float | None:
        if not isinstance(q, dict): return None
        for k in ("consensus_price", "price", "last"):
            if q.get(k) is not None:
                try: return float(q[k])
                except (TypeError, ValueError): pass
        return None
