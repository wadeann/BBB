from __future__ import annotations

from typing import Any, Callable, Protocol


class MCPError(RuntimeError):
    pass


class MCPInvoker(Protocol):
    def invoke(self, tool_name: str, **kwargs: Any) -> Any: ...


class RegistryMCPInvoker:
    """Adapter for production environments that can expose MCP tools as Python callables.

    This keeps the business runtime independent of stdio/SSE/Streamable HTTP details.
    Register wrappers whose signatures match the tool names from the v5 skill.
    """
    def __init__(self, registry: dict[str, Callable[..., Any]]):
        self.registry = registry

    def invoke(self, tool_name: str, **kwargs: Any) -> Any:
        fn = self.registry.get(tool_name)
        if not fn:
            raise MCPError(f"MCP tool not registered: {tool_name}")
        try:
            return fn(**kwargs)
        except Exception as exc:
            raise MCPError(f"{tool_name} failed: {exc}") from exc
