"""把应用 Tool 适配到官方 MCP SDK 的协议处理器。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp import types

from src.core.types import JsonDict
from src.mcp_server.tools import ToolArgumentError, ToolHandler
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ProtocolHandler:
    """管理 Tool Schema、调用路由和稳定的 JSON-RPC 错误语义。

    原始 JSON 解析、协议版本协商和 initialize wire 响应继续交给官方 SDK；本类只处理
    项目需要控制的应用层能力，避免重复实现 MCP 协议栈。
    """

    server_name: str
    server_version: str
    tools: dict[str, ToolHandler] = field(default_factory=dict)

    def register_tool(self, tool: ToolHandler) -> None:
        """注册一个 Tool，并在启动阶段拒绝重名或无效 Schema。"""
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
        """返回与 SDK initialize 响应一致的服务能力摘要，便于直接测试和内省。"""
        return {
            "serverInfo": {"name": self.server_name, "version": self.server_version},
            "capabilities": {"tools": {"listChanged": False}},
        }

    def handle_tools_list(self) -> types.ListToolsResult:
        """把已注册 Tool 转换为 MCP ``tools/list`` 的稳定 Schema。"""
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
        """路由 Tool 调用，并把边界异常转换为标准 JSON-RPC 错误。"""
        tool = self.tools.get(name)
        if tool is None:
            return types.ErrorData(
                code=types.METHOD_NOT_FOUND,
                message="Tool not found",
                data={"name": name},
            )

        try:
            # KnowledgeService 查询可能包含网络和磁盘 I/O，不能阻塞 SDK 的异步协议循环。
            raw_result = await asyncio.to_thread(tool.call, dict(arguments or {}))
        except ToolArgumentError as exc:
            logger.warning("Invalid arguments for tool %s: %s", name, exc)
            return types.ErrorData(
                code=types.INVALID_PARAMS,
                message="Invalid tool arguments",
                data={"name": name, "reason": str(exc)},
            )
        except Exception:
            # 完整异常只进入 stderr 日志；wire 响应不能泄漏堆栈、密钥或内部路径。
            logger.exception("Tool %s failed", name)
            return types.ErrorData(
                code=types.INTERNAL_ERROR,
                message="Internal server error",
                data={"name": name},
            )

        try:
            return types.CallToolResult.model_validate(raw_result)
        except Exception:
            logger.exception("Tool %s returned an invalid MCP result", name)
            return types.ErrorData(
                code=types.INTERNAL_ERROR,
                message="Internal server error",
                data={"name": name},
            )
