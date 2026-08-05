"""Primary MCP query Tool: routes parameters through the KnowledgeService."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from src.core.response import ResponseBuilder
from src.core.services.knowledge_service import KnowledgeService
from src.core.trace import TraceCollector, TraceContext
from src.core.types import JsonDict, QueryRequest, QueryResponse
from src.mcp_server.request_context import current_request_context
from src.mcp_server.tools.base import (
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    ToolArgumentError,
    ToolExecutionError,
    validate_identifier,
    validate_query,
    validate_top_k,
)
from src.observability.logger import get_logger
from src.observability.query_trace import (
    build_query_trace_metadata,
    classify_error_code,
    sanitize_results,
)

logger = get_logger(__name__)

_QUERY_PROPERTY: JsonDict = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_QUERY_CHARS,
    "description": "要查询的问题",
}
_TOP_K_PROPERTY: JsonDict = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_TOP_K,
    "default": 5,
    "description": "最多返回的相关片段数",
}
_COLLECTION_PROPERTY: JsonDict = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "default": "default",
    "description": "限定查询的知识集合",
}
_ALLOWED_ARGUMENTS: frozenset[str] = frozenset({"query", "top_k", "collection"})


class QueryKnowledgeHubTool:
    """Execute a query through the stable knowledge-service facade."""

    name = "query_knowledge_hub"
    description = "查询本地知识库，返回带来源引用的相关内容"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "query": _QUERY_PROPERTY,
            "top_k": _TOP_K_PROPERTY,
            "collection": _COLLECTION_PROPERTY,
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
        """Validate Tool inputs, run the service, and build an MCP result."""
        unknown = sorted(set(arguments) - _ALLOWED_ARGUMENTS)
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        query = validate_query(arguments.get("query"))
        top_k = validate_top_k(arguments.get("top_k"))
        collection = validate_identifier(
            arguments.get("collection", "default"), field="collection"
        )

        request = QueryRequest(query=query, top_k=top_k, collection=collection)
        try:
            response = _run_with_trace(self.get_service, request, self.get_collector)
        except ToolExecutionError:
            raise
        except Exception:
            logger.exception("query_knowledge_hub execution failed")
            raise ToolExecutionError("query_failed") from None
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
    request_context = current_request_context()
    try:
        response = service.query(request, trace=trace)
    except Exception as exc:
        trace.metadata = build_query_trace_metadata(
            request,
            request_context,
            status="failed",
            error_code=classify_error_code(exc),
            result_count=0,
            results=[],
        )
        if collector is not None:
            _collect_safely(collector, trace)
        # The wire must never see raw exception text (plan §6.7).
        logger.exception("query_knowledge_hub service raised: %s", exc)
        raise ToolExecutionError("query_failed") from None
    sanitized = sanitize_results(response.items)
    trace.metadata = build_query_trace_metadata(
        request,
        request_context,
        status="success",
        error_code=None,
        result_count=len(response.items),
        results=sanitized,
    )
    if collector is not None:
        _collect_safely(collector, trace)
    # Stamp trace_id onto the response so the caller can correlate it with
    # the SQLite row without leaking the trace object through the service.
    return replace(response, trace_id=trace.trace_id)


def _collect_safely(collector: TraceCollector, trace: TraceContext) -> None:
    """Forward traces without letting collector failures abort the query."""
    try:
        collector.collect(trace)
    except Exception:
        logger.warning("trace collector rejected an entry", exc_info=True)



__all__ = ["QueryKnowledgeHubTool"]
