"""基于官方 MCP SDK 的 Stdio Server 入口。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from src.core.services.knowledge_service import KnowledgeService
from src.mcp_server.protocol_handler import ProtocolHandler
from src.observability.logger import get_logger

SERVER_NAME = "modular-rag-mcp"
SERVER_VERSION = "0.1.0"

logger = get_logger(__name__)


@dataclass(frozen=True)
class ServerContext:
    """后续 Tool Handler 从官方 SDK lifespan 中取得的应用依赖。"""

    knowledge_service: KnowledgeService | None = None


def create_mcp_server(
    knowledge_service: KnowledgeService | None = None,
    protocol_handler: ProtocolHandler | None = None,
) -> Server[ServerContext]:
    """创建只负责协议生命周期的 MCP Server。

    E1 暂不注册业务 Tool；E2/E3 会在这个 Server 上增加协议处理和 Tool Handler。
    KnowledgeService 通过 lifespan 注入，避免 MCP 层自行构造检索和存储组件。
    """

    @asynccontextmanager
    async def lifespan(_: Server[ServerContext]) -> AsyncIterator[ServerContext]:
        yield ServerContext(knowledge_service=knowledge_service)

    handler = protocol_handler or ProtocolHandler(SERVER_NAME, SERVER_VERSION)

    async def on_list_tools(
        context: ServerRequestContext[ServerContext],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return handler.handle_tools_list()

    async def on_call_tool(
        context: ServerRequestContext[ServerContext],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        result = await handler.handle_tools_call(params.name, params.arguments)
        if isinstance(result, types.ErrorData):
            # 抛出 SDK 识别的异常后，transport 才会生成标准 JSON-RPC error envelope。
            raise MCPError.from_error_data(result)
        return result

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions="Query the local modular RAG knowledge base through MCP tools.",
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio_server(
    knowledge_service: KnowledgeService | None = None,
) -> None:
    """在 stdin/stdout 上运行 MCP；所有应用日志只写 stderr。"""
    server = create_mcp_server(knowledge_service)
    logger.info("Starting %s %s over stdio", SERVER_NAME, SERVER_VERSION)
    try:
        # 官方 transport 独占 stdout 作为 JSON-RPC wire，并把意外的普通输出转向 stderr。
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        logger.info("Stopped %s", SERVER_NAME)


def main() -> int:
    """控制台脚本和 ``python -m`` 共用的同步入口。"""
    asyncio.run(run_stdio_server())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
