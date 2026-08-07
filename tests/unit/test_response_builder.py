"""ResponseBuilder 的 MCP Markdown 与结构化引用测试。"""

from __future__ import annotations

from src.core.response import ResponseBuilder
from src.core.types import QueryResponse, RetrievalCandidate


def test_build_mcp_result_contains_retrieval_text_and_structured_results() -> None:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Lease generations fence stale workers.",
        metadata={"source_path": "manual.pdf", "page": 2},
        score=0.8,
        source="rerank",
        rank=1,
    )
    response = QueryResponse(
        results=[candidate],
        request_id="req-1",
        metadata={"collection": "docs"},
    )

    result = ResponseBuilder().build_mcp_result(response)

    assert result["content"] == [
        {
            "type": "text",
            "text": "检索结果：\n\n[1] manual.pdf，第 2 页\nLease generations fence stale workers.",
        }
    ]
    assert result["structuredContent"]["results"] == [
        {
            "rank": 1,
            "chunk_id": "chunk-1",
            "text": candidate.text,
            "score": 0.8,
            "score_kind": "rerank",
            "source": "manual.pdf",
            "page": 2,
            "metadata": {},
            "images": [],
        }
    ]
    assert result["structuredContent"]["request_id"] == "req-1"


def test_build_mcp_result_returns_friendly_text_for_empty_results() -> None:
    response = QueryResponse(
        results=[],
    )

    result = ResponseBuilder().build_mcp_result(response)

    assert result["content"][0]["text"] == "未找到相关知识库内容。"
    assert result["structuredContent"]["results"] == []
