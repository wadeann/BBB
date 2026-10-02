from __future__ import annotations

from pathlib import Path
from typing import Literal

from .config import RuntimeConfig, load_config
from .llm.base import LLMClient
from .llm.openai_compatible import OpenAICompatibleLLMClient
from .mcp.base import MCPInvoker
from .mcp.fake import FakeMCPInvoker
from .mcp.http import StreamableHTTPMCPInvoker
from .runtime import AgentRuntime

Backend = Literal["fake", "production"]


def configured_backend(project_root: str | Path) -> Backend:
    cfg = load_config(project_root)
    value = str(cfg.runtime.get("backend", "fake")).lower()
    return "production" if value == "production" else "fake"


def create_mcp_invoker(config: RuntimeConfig, *, backend: str | None = None) -> MCPInvoker:
    """Create only the MCP layer.

    Kept separate from full runtime construction so deployment probes can test MCP
    connectivity before any LLM credentials have been configured.
    """
    selected = str(backend or config.runtime.get("backend", "fake")).lower()
    if selected == "fake":
        return FakeMCPInvoker()
    if selected != "production":
        raise ValueError(f"unsupported backend: {selected}")
    return StreamableHTTPMCPInvoker(config.runtime.get("mcp") or {})


def create_llm_client(config: RuntimeConfig, *, required: bool = False, profile: str = "runtime") -> LLMClient | None:
    """Create only the OpenAI-compatible LLM layer.

    This function never reads MCP credentials and is therefore safe to use for an
    isolated LLM deployment probe.
    """
    llm_cfg = dict(config.runtime.get("llm") or {})
    if profile == "research":
        overrides = llm_cfg.get("research_overrides") or {}
        if isinstance(overrides, dict):
            llm_cfg.update(overrides)
    if not bool(llm_cfg.get("enabled", True)):
        if required:
            raise ValueError("LLM is disabled in config/runtime.yaml")
        return None
    provider = str(llm_cfg.get("provider", "openai_compatible")).lower()
    if provider != "openai_compatible":
        raise ValueError(f"unsupported LLM provider: {provider}")
    return OpenAICompatibleLLMClient(llm_cfg)


def create_runtime(project_root: str | Path, *, backend: str | None = None, include_llm: bool = True) -> AgentRuntime:
    root = Path(project_root).resolve()
    cfg = load_config(root)
    selected = str(backend or cfg.runtime.get("backend", "fake")).lower()
    mcp = create_mcp_invoker(cfg, backend=selected)
    llm = create_llm_client(cfg) if selected == "production" and include_llm else None
    return AgentRuntime(root, mcp, llm)
