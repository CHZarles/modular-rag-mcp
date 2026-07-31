"""query_knowledge_hub Tool 的服务边界和参数测试。"""

from __future__ import annotations

import pytest

from src.core.types import (
    Citation,
    CollectionInfo,
    DocumentSummary,
    JsonDict,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.mcp_server.tools import QueryKnowledgeHubTool, ToolArgumentError, ToolHandler


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

    result = tool.call({"query": "  generation fence  ", "top_k": 2, "collection": "docs"})

    assert isinstance(tool, ToolHandler)
    assert service.requests == [
        QueryRequest(query="generation fence", top_k=2, collection="docs")
    ]
    assert result["content"][0]["type"] == "text"
    assert "[1]" in result["content"][0]["text"]
    citation = result["structuredContent"]["citations"][0]
    assert citation["source"] == "manual.pdf"
    assert citation["page"] == 3
    assert citation["chunk_id"] == "chunk-1"
    assert citation["score"] == 0.9


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": " "},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": True},
        {"query": "x", "collection": " "},
        {"query": "x", "unknown": 1},
    ],
)
def test_tool_rejects_invalid_arguments(arguments: JsonDict) -> None:
    tool = QueryKnowledgeHubTool(lambda: FakeKnowledgeService(_response()))

    with pytest.raises(ToolArgumentError):
        tool.call(arguments)


def _response() -> QueryResponse:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Generation fencing prevents stale publication.",
        metadata={"source_path": "manual.pdf", "page": 3},
        score=0.9,
        source="fusion",
        rank=1,
    )
    citation = Citation(
        citation_id="c1",
        chunk_id="chunk-1",
        source_path="manual.pdf",
        page=3,
        text="Generation fencing prevents stale publication.",
        score=0.9,
    )
    return QueryResponse(
        answer="Generation fencing blocks stale workers.",
        citations=[citation],
        items=[candidate],
        request_id="req-1",
    )
