"""基于官方 MCP SDK 的 Stdio Server 入口。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from src.application.upload_ingestion import UploadIngestionCoordinator
from src.core.services.grep_service import GrepService
from src.core.services.knowledge_service import KnowledgeService
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools.get_document_summary import GetDocumentSummaryTool
from src.mcp_server.tools.grep_knowledge_hub import GrepKnowledgeHubTool
from src.mcp_server.tools.ingestion_jobs import GetIngestionJobTool, UploadDocumentTool
from src.mcp_server.tools.list_collections import ListCollectionsTool
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool
from src.observability.logger import get_logger

SERVER_NAME = "modular-rag-mcp"
SERVER_VERSION = "0.1.1"
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config/settings.yaml"

logger = get_logger(__name__)


@dataclass(frozen=True)
class ServerContext:
    """后续 Tool Handler 从官方 SDK lifespan 中取得的应用依赖。"""

    knowledge_service: KnowledgeService | None = None


def create_mcp_server(
    knowledge_service: KnowledgeService | None = None,
    protocol_handler: ProtocolHandler | None = None,
    ingestion_coordinator: UploadIngestionCoordinator | None = None,
    grep_service: GrepService | None = None,
) -> Server[ServerContext]:
    """创建负责协议生命周期、Tool 注册和应用依赖注入的 MCP Server。

    KnowledgeService 通过 lifespan 注入，避免 MCP 层自行构造检索和存储组件。
    """

    coordinator = ingestion_coordinator
    owns_coordinator = False
    coordinator_lock = Lock()

    def get_ingestion_coordinator() -> UploadIngestionCoordinator:
        nonlocal coordinator, owns_coordinator
        if coordinator is None:
            with coordinator_lock:
                if coordinator is None:
                    coordinator = _build_default_ingestion_coordinator()
                    owns_coordinator = True
        return coordinator

    @asynccontextmanager
    async def lifespan(_: Server[ServerContext]) -> AsyncIterator[ServerContext]:
        try:
            yield ServerContext(knowledge_service=knowledge_service)
        finally:
            if owns_coordinator and coordinator is not None:
                await asyncio.to_thread(coordinator.shutdown, wait=True)

    handler = protocol_handler or ProtocolHandler(SERVER_NAME, SERVER_VERSION)
    resolved_grep_service = grep_service or _build_default_grep_service_if_available()
    get_service = (
        (lambda: knowledge_service)
        if knowledge_service is not None
        else _build_default_knowledge_service
    )
    get_collector = _build_default_trace_collector
    if QueryKnowledgeHubTool.name not in handler.tools:
        handler.register_tool(QueryKnowledgeHubTool(get_service, get_collector=get_collector))
    if ListCollectionsTool.name not in handler.tools:
        handler.register_tool(ListCollectionsTool(get_service))
    if GetDocumentSummaryTool.name not in handler.tools:
        handler.register_tool(GetDocumentSummaryTool(get_service))
    if (
        resolved_grep_service is not None
        and GrepKnowledgeHubTool.name not in handler.tools
    ):
        handler.register_tool(
            GrepKnowledgeHubTool(
                lambda: resolved_grep_service,
                get_collector=get_collector,
            )
        )
    if UploadDocumentTool.name not in handler.tools:
        handler.register_tool(UploadDocumentTool(get_ingestion_coordinator))
    if GetIngestionJobTool.name not in handler.tools:
        handler.register_tool(GetIngestionJobTool(get_ingestion_coordinator))

    async def on_list_tools(
        context: ServerRequestContext[ServerContext],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return handler.handle_tools_list()

    async def on_call_tool(
        context: ServerRequestContext[ServerContext],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        result = await handler.handle_tools_call(
            params.name,
            params.arguments,
            request_headers=_extract_request_headers(context),
        )
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


@lru_cache(maxsize=1)
def _build_default_knowledge_service() -> KnowledgeService:
    """首次 Tool 调用时才装配本地检索，保证 initialize 不依赖外部模型服务。"""
    from src.core.services import build_knowledge_service
    from src.core.settings import load_settings

    settings_path = os.environ.get("RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH))
    settings = load_settings(settings_path)
    return build_knowledge_service(settings)


@lru_cache(maxsize=1)
def _build_default_trace_collector():
    """配置启用时共享同一个 TraceCollector，所有 MCP 工具共用同一份 JSONL。"""
    from src.core.settings import load_settings
    from src.observability.ingestion_trace import create_trace_collector

    settings_path = os.environ.get("RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH))
    settings = load_settings(settings_path)
    return create_trace_collector(settings)


def _build_default_ingestion_coordinator() -> UploadIngestionCoordinator:
    """Build upload dependencies only when the first upload Tool is called."""
    from collections.abc import Mapping

    from src.core.settings import load_settings
    from src.ingestion import build_ingestion_pipeline
    from src.observability.ingestion_trace import create_ingestion_trace_collector

    settings_path = os.environ.get("RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH))
    settings = load_settings(settings_path)
    storage = settings.ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")
    upload_root = storage.get("upload_root")
    if not isinstance(upload_root, str) or not upload_root.strip():
        raise ValueError("Missing required setting: ingestion.storage.upload_root")
    return UploadIngestionCoordinator(
        settings,
        upload_root.strip(),
        build_ingestion_pipeline,
        collector=create_ingestion_trace_collector(settings),
    )


def _build_default_grep_service_if_available() -> GrepService | None:
    """Build only local grep state; any failure leaves existing Tools untouched."""
    try:
        from collections.abc import Mapping

        from src.core.services import active_generation_counts
        from src.core.settings import load_settings
        from src.ingestion.storage import SQLiteGrepIndex
        from src.libs.loader import SQLiteIntegrityStore

        settings_path = os.environ.get("RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH))
        settings = load_settings(settings_path)
        if settings.grep.get("enabled") is not True:
            return None
        storage = settings.ingestion.get("storage")
        if not isinstance(storage, Mapping):
            raise ValueError("Missing required setting: ingestion.storage")
        integrity_path = storage.get("integrity_db_path")
        if not isinstance(integrity_path, str) or not integrity_path.strip():
            raise ValueError("Missing required setting: ingestion.storage.integrity_db_path")
        db_path = settings.grep.get("db_path")
        timeout_ms = settings.grep.get("timeout_ms", 1000)
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("Missing required setting: grep.db_path")
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            raise ValueError("Setting grep.timeout_ms must be a positive integer")
        integrity = SQLiteIntegrityStore(
            integrity_path,
            timeout_seconds=timeout_ms / 1000.0,
        )
        index = SQLiteGrepIndex(db_path, timeout_ms=timeout_ms)
        active, counts = active_generation_counts(integrity)
        index.check_ready(active, counts)
        return GrepService(index, integrity)
    except Exception:
        logger.warning("Grep is unavailable; existing MCP tools remain enabled", exc_info=True)
        return None


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


def _extract_request_headers(context: ServerRequestContext[ServerContext]) -> Mapping[str, str] | None:
    """Return the raw HTTP Headers for the MCP HTTP transport.

    Streamable HTTP attaches the inbound FastAPI ``Request`` to
    ``ServerRequestContext.request``. Stdio leaves ``context.request``
    ``None``; in that case the protocol handler falls back to a synthetic
    request id (plan §5.5 / FR-13).
    """
    request = context.request
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    return {key: value for key, value in headers.items()}


def main() -> int:
    """控制台脚本和 ``python -m`` 共用的同步入口。"""
    asyncio.run(run_stdio_server())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
