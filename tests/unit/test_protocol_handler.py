"""ProtocolHandler 的 Tool 注册、路由与错误码测试。"""

from __future__ import annotations

import asyncio

import pytest
from mcp import types

from src.core.types import JsonDict
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import ToolArgumentError, ToolHandler


class EchoTool:
    name = "echo"
    description = "返回输入文本"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def call(self, arguments: JsonDict) -> JsonDict:
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ToolArgumentError("text is required")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"echo": text},
        }


class ExplodingTool(EchoTool):
    name = "explode"

    def call(self, arguments: JsonDict) -> JsonDict:
        raise RuntimeError("secret database path: /private/data.db")


def test_initialize_and_tools_list_expose_stable_capabilities() -> None:
    handler = ProtocolHandler("test-server", "1.2.3")
    tool = EchoTool()
    handler.register_tool(tool)

    assert isinstance(tool, ToolHandler)
    assert handler.handle_initialize() == {
        "serverInfo": {"name": "test-server", "version": "1.2.3"},
        "capabilities": {"tools": {"listChanged": False}},
    }
    result = handler.handle_tools_list()
    assert len(result.tools) == 1
    assert result.tools[0].name == "echo"
    assert result.tools[0].description == "返回输入文本"
    assert result.tools[0].input_schema == EchoTool.input_schema

    with pytest.raises(ValueError, match="already registered"):
        handler.register_tool(EchoTool())


def test_tools_call_routes_result_and_maps_argument_errors() -> None:
    handler = ProtocolHandler("test-server", "1.0")
    handler.register_tool(EchoTool())

    result = asyncio.run(handler.handle_tools_call("echo", {"text": "hello"}))
    assert isinstance(result, types.CallToolResult)
    assert isinstance(result.content[0], types.TextContent)
    assert result.content[0].type == "text"
    assert result.content[0].text == "hello"
    assert result.structured_content == {"echo": "hello"}

    invalid = asyncio.run(handler.handle_tools_call("echo", {}))
    assert isinstance(invalid, types.ErrorData)
    assert invalid.code == types.INVALID_PARAMS


def test_tools_call_returns_standard_errors_without_leaking_internal_details() -> None:
    handler = ProtocolHandler("test-server", "1.0")
    handler.register_tool(ExplodingTool())

    missing = asyncio.run(handler.handle_tools_call("missing", {}))
    assert isinstance(missing, types.ErrorData)
    assert missing.code == types.METHOD_NOT_FOUND

    failed = asyncio.run(handler.handle_tools_call("explode", {}))
    assert isinstance(failed, types.ErrorData)
    assert failed.code == types.INTERNAL_ERROR
    assert "secret" not in failed.message
    assert "/private" not in str(failed.data)
