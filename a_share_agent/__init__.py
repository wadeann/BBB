"""A-share Agent Runtime.

The runtime is intentionally transport-agnostic: MCP and LLM clients are injected.
Production side effects are guarded by deterministic phase permissions, risk checks,
idempotent intents, and append-only audit logging.
"""

__version__ = "0.7.3"
