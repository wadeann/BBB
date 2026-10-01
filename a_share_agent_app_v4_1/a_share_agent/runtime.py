from __future__ import annotations

from pathlib import Path
from typing import Any

from .audit.event_writer import AuditEventWriter
from .audit.manifest import DailyManifestBuilder
from .audit.replay import ReplayEngine
from .config import load_config
from .core.orchestrator import AgentOrchestrator
from .core.permissions import PhasePermissions
from .core.recovery import RecoveryManager
from .core.scheduler import Scheduler
from .execution.engine import ExecutionEngine
from .execution.intent_store import IntentStore
from .mcp.base import MCPInvoker
from .llm.base import LLMClient
from .llm.decision_agent import DecisionAgent
from .review.daily import DailyReviewer
from .risk.engine import LocalRiskEngine
from .strategy.signal_engine import DeterministicSignalEngine
from .worker_state import WorkerStateStore
from .notifications.dispatcher import NotificationDispatcher


class AgentRuntime:
    def __init__(self, project_root: str | Path, mcp: MCPInvoker, llm: LLMClient | None = None):
        self.config = load_config(project_root)
        self.mcp = mcp
        self.permissions = PhasePermissions(self.config.permissions)
        self.scheduler = Scheduler(self.config.schedule, self.permissions, mode=self.config.mode)
        self.audit = AuditEventWriter(self.config)
        self.decision_agent = DecisionAgent(self.config, llm, self.audit) if llm is not None else None
        self.replay = ReplayEngine(self.audit.audit_root, self.audit.index, self.audit.snapshot_store)
        self.reviewer = DailyReviewer(self.config, self.replay)
        self.manifest = DailyManifestBuilder(self.config, self.replay)
        self.intents = IntentStore(self.audit.audit_root / "intent_store.sqlite")
        self.local_risk = LocalRiskEngine(self.config)
        self.signal_engine = DeterministicSignalEngine()
        self.recovery = RecoveryManager(mcp, self.intents)
        self.execution = ExecutionEngine(self.config, mcp, self.audit, self.permissions, self.intents, self.local_risk)
        self.orchestrator = AgentOrchestrator(self.config, mcp, self.audit, self.replay, self.reviewer, self.manifest, self.recovery)
        worker_db = self.config.runtime.get("worker", {}).get("state_db", "data/audit/worker_state.sqlite")
        self.worker_state = WorkerStateStore(self.config.project_root / worker_db)
        self.notifications = NotificationDispatcher(self.config, self.audit.index)

    def run_phase(self, phase: str, **kwargs: Any) -> dict[str, Any]:
        ctx = self.scheduler.make_run_context(phase)
        return self.orchestrator.run_phase(ctx, **kwargs)

    def execute_signal(self, phase: str, signal: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        ctx = self.scheduler.make_run_context(phase)
        return self.execution.execute_signal(ctx, signal, route)
