"""Hermes Platform — MCP client, device doctor, profile builder, model advisor.

Standalone package: imports nothing from agent_ultimate. Stdlib only.
"""
from .mcp_client import McpClient
from .doctor import DependencyScanner
from .profiler import ProfileBuilder, PERSONAS
from .modeladvisor import ModelAdvisor

__all__ = ["McpClient", "DependencyScanner", "ProfileBuilder", "PERSONAS", "ModelAdvisor"]
