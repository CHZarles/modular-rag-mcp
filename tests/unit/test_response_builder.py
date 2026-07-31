"""ResponseBuilder 的 MCP Markdown 与结构化引用测试。"""

from __future__ import annotations

from src.core.response import ResponseBuilder
from src.core.types import Citation, QueryResponse, RetrievalCandidate


def test_build_mcp_result_contains_markdown_markers_and_structured_citations() -> None:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Lease generations fence stale workers.",
        metadata={"source_path": "manual.pdf", "page": 2},
        score=0.8,
        source="rerank",
        rank=1,
    )
    response = QueryResponse(
        answer="Use a generation token.",
        citations=[
            Citation(
                citation_id="c1",
                chunk_id="chunk-1",
                source_path="manual.pdf",
                page=2,
                text=candidate.text,
                score=0.8,
            )
        ],
        items=[candidate],
        request_id="req-1",
        metadata={"collection": "docs"},
    )

    result = ResponseBuilder().build_mcp_result(response)

    assert result["content"] == [
        {
            "type": "text",
            "text": "Use a generation token.\n\n### 引用\n[1] manual.pdf，第 2 页",
        }
    ]
    assert result["structuredContent"]["citations"] == [
        {
            "id": "c1",
            "source": "manual.pdf",
            "page": 2,
            "chunk_id": "chunk-1",
            "score": 0.8,
            "text": candidate.text,
            "metadata": {},
        }
    ]
    assert result["structuredContent"]["request_id"] == "req-1"


def test_build_mcp_result_returns_friendly_text_for_empty_results() -> None:
    response = QueryResponse(
        answer="No relevant context found.",
        citations=[],
        items=[],
    )

    result = ResponseBuilder().build_mcp_result(response)

    assert result["content"][0]["text"] == "未找到相关知识库内容。"
    assert result["structuredContent"]["citations"] == []
