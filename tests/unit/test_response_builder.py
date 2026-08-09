"""ResponseBuilder 的 MCP Markdown 与结构化引用测试。"""

from __future__ import annotations

from src.core.response import ResponseBuilder
from src.core.trace import TraceContext
from src.core.types import QueryRequest, QueryResponse, RetrievalCandidate


def test_build_mcp_result_contains_retrieval_text_and_structured_results() -> None:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Lease generations fence stale workers.",
        metadata={"source_path": "manual.pdf", "page": 2},
        score=0.8,
        source="rerank",
        rank=1,
        debug={
            "score_kind": "distance",
            "rrf": {
                "k": 60,
                "score": 0.032,
                "sources": [
                    {"source": "dense", "rank": 2, "score": 0.21},
                    {"source": "sparse", "rank": 4, "score": 3.7},
                ],
            },
            "rerank": {
                "backend": "cross_encoder",
                "fallback": False,
                "score": 0.8,
                "previous_score": 0.032,
            },
        },
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
            "score_stages": [
                {
                    "stage": "dense",
                    "status": "hit",
                    "rank": 2,
                    "score": 0.21,
                    "score_kind": "distance",
                    "rrf_contribution": 1 / 62,
                    "k": None,
                    "backend": None,
                },
                {
                    "stage": "bm25",
                    "status": "hit",
                    "rank": 4,
                    "score": 3.7,
                    "score_kind": "bm25",
                    "rrf_contribution": 1 / 64,
                    "k": None,
                    "backend": None,
                },
                {
                    "stage": "fusion",
                    "status": "success",
                    "rank": None,
                    "score": 0.032,
                    "score_kind": "rrf",
                    "rrf_contribution": None,
                    "k": 60,
                    "backend": None,
                },
                {
                    "stage": "rerank",
                    "status": "success",
                    "rank": 1,
                    "score": 0.8,
                    "score_kind": "rerank",
                    "rrf_contribution": None,
                    "k": None,
                    "backend": "cross_encoder",
                },
            ],
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


def test_build_mcp_result_marks_a_disabled_retrieval_route_as_skipped() -> None:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="BM25 hit",
        metadata={},
        score=1 / 61,
        source="fusion",
        rank=1,
        debug={
            "score_kind": "bm25",
            "rrf": {
                "k": 60,
                "score": 1 / 61,
                "sources": [{"source": "sparse", "rank": 1, "score": 4.2}],
            },
        },
    )
    trace = TraceContext()
    trace.record_stage("sparse_retrieval", {"details": {"status": "success"}})

    response = ResponseBuilder().build(QueryRequest(query="bm25"), [candidate], trace)
    stages = ResponseBuilder().build_mcp_result(response)["structuredContent"]["results"][0][
        "score_stages"
    ]

    assert [(stage["stage"], stage["status"]) for stage in stages[:2]] == [
        ("dense", "skipped"),
        ("bm25", "hit"),
    ]
