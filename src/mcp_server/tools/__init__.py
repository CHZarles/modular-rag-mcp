"""MCP Tool 适配器契约。"""

from src.mcp_server.tools.base import ToolArgumentError, ToolHandler
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

__all__ = ["QueryKnowledgeHubTool", "ToolArgumentError", "ToolHandler"]
