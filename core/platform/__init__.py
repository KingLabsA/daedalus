"""Hermes Platform — MCP client, device doctor, profile builder, model advisor.

Standalone package: imports nothing from agent_ultimate. Stdlib only.
"""

from .doctor import DependencyScanner
from .mcp_client import McpClient
from .modeladvisor import ModelAdvisor
from .profiler import PERSONAS, ProfileBuilder

__all__ = ["McpClient", "DependencyScanner", "ProfileBuilder", "PERSONAS", "ModelAdvisor"]
