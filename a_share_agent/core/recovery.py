from __future__ import annotations

from typing import Any

from ..execution.intent_store import IntentStore
from ..mcp.base import MCPInvoker


class RecoveryManager:
    def __init__(self, mcp: MCPInvoker, intents: IntentStore):
        self.mcp, self.intents = mcp, intents

    def sync(self) -> dict[str, Any]:
        remote = {
            "balance": self.mcp.invoke("mcp_exec_get_balance"),
            "positions": self.mcp.invoke("mcp_exec_get_positions"),
            "pending_orders": self.mcp.invoke("mcp_exec_get_orders", status="PENDING"),
            "trades": self.mcp.invoke("mcp_exec_get_today_trades"),
            "pnl": self.mcp.invoke("mcp_exec_get_pnl", period="today"),
        }
        # The local intent store is intentionally not auto-mutated here. Unknown remote
        # orders or fills are surfaced for deterministic reconciliation by operator/code.
        known_intents = {str(o.get("intent_id")) for o in remote["pending_orders"] if isinstance(o, dict) and o.get("intent_id")}
        known_intents |= {str(t.get("intent_id")) for t in remote["trades"] if isinstance(t, dict) and t.get("intent_id")}
        unresolved=[]
        for iid in known_intents:
            if not self.intents.get(iid): unresolved.append({"type":"REMOTE_INTENT_NOT_LOCAL","intent_id":iid})
        return {"state":"SYNCED" if not unresolved else "DEGRADED_NO_ORDER","remote":remote,"unresolved":unresolved}
