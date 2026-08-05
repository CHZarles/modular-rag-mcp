"""Bridge registered Tool adapters into the official MCP SDK wire format."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp import types

from src.core.types import JsonDict
from src.mcp_server.tools import ToolArgumentError, ToolExecutionError, ToolHandler
from src.mcp_server.tools.base import sanitize_wire_string
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ProtocolHandler:
    """Manage Tool Schemas, dispatch calls, and produce stable JSON-RPC errors.

    The official SDK still owns JSON parsing, capability negotiation, and the
    initialize wire response. This class only owns the application-level mapping
    that the project must control directly.
    """

    server_name: str
    server_version: str
    tools: dict[str, ToolHandler] = field(default_factory=dict)

    def register_tool(self, tool: ToolHandler) -> None:
        """Register a Tool, rejecting duplicates or invalid Schemas at startup."""
        name = tool.name.strip()
        description = tool.description.strip()
        if not name:
            raise ValueError("tool name must not be empty")
        if not description:
            raise ValueError(f"tool {name!r} description must not be empty")
        if tool.input_schema.get("type") != "object":
            raise ValueError(f"tool {name!r} input_schema must describe an object")
        if name in self.tools:
            raise ValueError(f"tool already registered: {name}")
        self.tools[name] = tool

    def handle_initialize(self, params: Mapping[str, Any] | None = None) -> JsonDict:
        """Return a capabilities summary that mirrors the SDK initialize response."""
        return {
            "serverInfo": {"name": self.server_name, "version": self.server_version},
            "capabilities": {"tools": {"listChanged": False}},
        }

    def handle_tools_list(self) -> types.ListToolsResult:
        """Render registered Tools as the stable ``tools/list`` payload."""
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                )
                for tool in self.tools.values()
            ]
        )

    async def handle_tools_call(
        self,
        name: str,
        arguments: JsonDict | None,
    ) -> types.CallToolResult | types.ErrorData:
        """Route a Tool call and map boundary failures to stable wire errors.

        Error envelopes intentionally carry only the Tool name and a stable
        ``component_code`` (see plan §5.4). All raw exception text is logged to
        stderr where operators can read it; the wire never sees internals.
        """
        tool = self.tools.get(name)
        if tool is None:
            return _wire_error(
                code=types.METHOD_NOT_FOUND,
                component_code="tool_not_found",
                name=name,
                message="Unknown tool",
            )

        try:
            raw_result = await asyncio.to_thread(tool.call, dict(arguments or {}))
        except ToolArgumentError as exc:
            logger.warning("Invalid arguments for tool %s: %s", name, exc)
            return _wire_error(
                code=types.INVALID_PARAMS,
                component_code="invalid_params",
                name=name,
                message="Invalid tool arguments",
            )
        except ToolExecutionError as exc:
            logger.error(
                "Tool %s failed with component code %s", name, exc.component_code
            )
            return _wire_error(
                code=types.INTERNAL_ERROR,
                component_code=exc.component_code,
                name=name,
                message="Internal server error",
            )
        except Exception:
            logger.exception("Tool %s failed", name)
            return _wire_error(
                code=types.INTERNAL_ERROR,
                component_code="internal_error",
                name=name,
                message="Internal server error",
            )

        try:
            return types.CallToolResult.model_validate(raw_result)
        except Exception:
            logger.exception("Tool %s returned an invalid MCP result", name)
            return _wire_error(
                code=types.INTERNAL_ERROR,
                component_code="internal_error",
                name=name,
                message="Internal server error",
            )


def _wire_error(
    *,
    code: int,
    component_code: str,
    name: str,
    message: str,
) -> types.ErrorData:
    """Compose the wire envelope with only the public-safe fields."""
    return types.ErrorData(
        code=code,
        message=sanitize_wire_string(message),
        data={"name": name, "component_code": component_code},
    )
