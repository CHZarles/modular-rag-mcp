"""MCP 主查询 Tool：把协议参数转换成 KnowledgeService 请求。"""

from __future__ import annotations

from collections.abc import Callable

from src.core.response import ResponseBuilder
from src.core.services.knowledge_service import KnowledgeService
from src.core.trace import TraceCollector, TraceContext
from src.core.types import JsonDict, QueryRequest, QueryResponse
from src.mcp_server.tools.base import ToolArgumentError
from src.observability.logger import get_logger

logger = get_logger(__name__)


class QueryKnowledgeHubTool:
    """通过稳定应用服务执行查询，不接触具体 Retriever 或存储实现。"""

    name = "query_knowledge_hub"
    description = "查询本地知识库，返回带来源引用的相关内容"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "description": "要查询的问题"},
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "default": 5,
                "description": "最多返回的相关片段数",
            },
            "collection": {
                "type": "string",
                "minLength": 1,
                "default": "default",
                "description": "限定查询的知识集合",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        get_service: Callable[[], KnowledgeService],
        response_builder: ResponseBuilder | None = None,
        get_collector: Callable[[], TraceCollector | None] | None = None,
    ) -> None:
        self.get_service = get_service
        self.response_builder = response_builder or ResponseBuilder()
        self.get_collector = get_collector

    def call(self, arguments: JsonDict) -> JsonDict:
        """校验 Tool 参数，调用 KnowledgeService 并构建 MCP 结果。"""
        unknown = sorted(set(arguments) - {"query", "top_k", "collection"})
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolArgumentError("query must be a non-empty string")

        top_k = arguments.get("top_k", 5)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ToolArgumentError("top_k must be a positive integer")

        collection = arguments.get("collection", "default")
        if not isinstance(collection, str) or not collection.strip():
            raise ToolArgumentError("collection must be a non-empty string")

        request = QueryRequest(
            query=query.strip(),
            top_k=top_k,
            collection=collection.strip(),
        )
        response = _run_with_trace(self.get_service, request, self.get_collector)
        return self.response_builder.build_mcp_result(response)


def _run_with_trace(
    get_service: Callable[[], KnowledgeService],
    request: QueryRequest,
    get_collector: Callable[[], TraceCollector | None] | None,
) -> QueryResponse:
    service = get_service()
    collector = get_collector() if get_collector is not None else None
    trace = TraceContext(
        trace_type="query",
        metadata={
            "query": request.query,
            "collection": request.collection,
            "top_k": request.top_k,
        },
    )
    try:
        response = service.query(request, trace=trace)
    except Exception as exc:
        trace.metadata.update({"status": "failed", "error": str(exc)})
        if collector is not None:
            _collect_safely(collector, trace)
        raise
    trace.metadata.update(
        {
            "status": "success",
            "result_count": len(response.items),
        }
    )
    if collector is not None:
        _collect_safely(collector, trace)
    return response


def _collect_safely(collector: TraceCollector, trace: TraceContext) -> None:
    try:
        collector.collect(trace)
    except Exception as exc:
        logger.warning("Unable to persist query trace %s: %s", trace.trace_id, exc)
