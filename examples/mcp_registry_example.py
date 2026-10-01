"""Replace wrappers below with your actual MCP client calls."""
from a_share_agent.mcp.base import RegistryMCPInvoker


def build_mcp(tool_functions: dict):
    # tool_functions should map the exact 53 names to wrappers supplied by your host.
    return RegistryMCPInvoker(tool_functions)
