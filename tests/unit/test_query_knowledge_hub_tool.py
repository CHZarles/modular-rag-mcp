"""query_knowledge_hub Tool 的服务边界和参数测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    JsonDict,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.mcp_server.tools import QueryKnowledgeHubTool, ToolArgumentError, ToolHandler


class _FakeCollector:
    def __init__(self) -> None:
        self.records: list[JsonDict] = []

    def collect(self, trace: object) -> None:
        if hasattr(trace, "to_dict"):
            self.records.append(trace.to_dict())  # type: ignore[arg-type]
        else:  # pragma: no cover - collector contracts always supply to_dict
            self.records.append({"trace_type": getattr(trace, "trace_type", "query")})


class FakeKnowledgeService:
    def __init__(self, response: QueryResponse) -> None:
        self.response = response
        self.requests: list[QueryRequest] = []

    def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
        self.requests.append(request)
        return self.response

    def list_collections(self) -> list[CollectionInfo]:  # pragma: no cover
        raise AssertionError("list_collections must not be called")

    def get_document_summary(self, doc_id: str) -> DocumentSummary:  # pragma: no cover
        raise AssertionError("get_document_summary must not be called")


def test_tool_only_calls_knowledge_service_and_returns_cited_mcp_result() -> None:
    service = FakeKnowledgeService(_response())
    tool = QueryKnowledgeHubTool(lambda: service)

    result = tool.call(
        {
            "query": "  generation fence  ",
            "top_k": 2,
            "collection": "docs",
            "file_type": "pdf",
        }
    )

    assert isinstance(tool, ToolHandler)
    assert service.requests == [
        QueryRequest(
            query="generation fence",
            top_k=2,
            collection="docs",
            filters={"doc_type": "pdf"},
        )
    ]
    assert result["content"][0]["type"] == "text"
    assert "[1]" in result["content"][0]["text"]
    item = result["structuredContent"]["results"][0]
    assert item["source"] == "manual.pdf"
    assert item["page"] == 3
    assert item["chunk_id"] == "chunk-1"
    assert item["score"] == 0.9


def test_tool_passes_trace_when_collector_is_available(tmp_path: Path) -> None:
    service = FakeKnowledgeService(_response())
    collector = _FakeCollector()
    tool = QueryKnowledgeHubTool(lambda: service, get_collector=lambda: collector)

    result = tool.call({"query": "trace me", "collection": "docs"})

    assert collector.records, "collector should receive the trace"
    payload = collector.records[-1]
    assert payload["trace_type"] == "query"
    assert payload["metadata"]["query"] == "trace me"
    assert payload["metadata"]["collection"] == "docs"
    assert payload["metadata"]["status"] == "success"
    assert payload["metadata"]["result_count"] == 1
    # Trace_id is stamped onto the structuredContent so MCP clients can
    # correlate the response with the persisted SQLite row.
    trace_id = result["structuredContent"]["trace_id"]
    assert trace_id == payload["trace_id"]


def test_tool_returns_response_with_trace_id_in_structured_content() -> None:
    service = FakeKnowledgeService(_response())
    tool = QueryKnowledgeHubTool(lambda: service)

    result = tool.call({"query": "  Generation Fence  ", "collection": "docs"})

    # Trace_id is always stamped so the wire shape is stable regardless of
    # whether the collector is wired; the SQLite row simply is not written
    # when the collector is None.
    trace_id = result["structuredContent"]["trace_id"]
    assert isinstance(trace_id, str) and trace_id
    # request_id still comes from the response so the wire shape is stable.
    assert result["structuredContent"]["request_id"] == "req-1"


def test_tool_collector_failure_does_not_break_query() -> None:
    class _ExplodingCollector:
        def collect(self, trace: object) -> None:
            raise RuntimeError("collector down")

    service = FakeKnowledgeService(_response())
    tool = QueryKnowledgeHubTool(
        lambda: service,
        get_collector=lambda: _ExplodingCollector(),
    )

    result = tool.call({"query": "hi", "collection": "docs"})

    # Response shape is unchanged; trace_id still stamped.
    assert result["content"][0]["type"] == "text"
    assert result["structuredContent"]["trace_id"] is not None


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": " "},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": True},
        {"query": "x", "collection": " "},
        {"query": "x", "file_type": "pptx"},
        {"query": "x", "unknown": 1},
    ],
)
def test_tool_rejects_invalid_arguments(arguments: JsonDict) -> None:
    tool = QueryKnowledgeHubTool(lambda: FakeKnowledgeService(_response()))

    with pytest.raises(ToolArgumentError):
        tool.call(arguments)


def test_tool_maps_service_exception_to_stable_component_code() -> None:
    class _RaisingService(FakeKnowledgeService):
        def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
            self.requests.append(request)
            raise LookupError("missing collection")

    from src.mcp_server.tools.base import ToolExecutionError

    tool = QueryKnowledgeHubTool(lambda: _RaisingService(_response()))

    with pytest.raises(ToolExecutionError) as excinfo:
        tool.call({"query": "hi", "collection": "docs"})

    assert excinfo.value.component_code == "query_failed"


def _response() -> QueryResponse:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Generation fencing prevents stale publication.",
        metadata={"source_path": "manual.pdf", "page": 3},
        score=0.9,
        source="fusion",
        rank=1,
    )
    return QueryResponse(
        results=[candidate],
        request_id="req-1",
    )
