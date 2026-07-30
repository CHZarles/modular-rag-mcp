"""QueryProcessor 的规则分词与过滤条件契约测试。"""

from __future__ import annotations

from src.core.query_engine import QueryProcessor
from src.core.types import ProcessedQuery, QueryRequest
from src.ports.query import QueryProcessor as QueryProcessorPort


def test_process_normalizes_query_and_extracts_unique_keywords() -> None:
    processor = QueryProcessor()

    result = processor.process(
        QueryRequest(query="  How do I configure Azure OpenAI, AZURE?  ")
    )

    assert result == ProcessedQuery(
        original_query="  How do I configure Azure OpenAI, AZURE?  ",
        standalone_query="How do I configure Azure OpenAI, AZURE?",
        keywords=["configure", "Azure", "OpenAI"],
        filters={},
    )


def test_process_preserves_chinese_and_versioned_technical_terms() -> None:
    result = QueryProcessor().process(
        QueryRequest(query="如何配置 MiniMax-M3 与 RAG？")
    )

    # 中文连续文本留给 BM25 的统一 tokenizer 继续拆分，避免查询侧另造分词规则。
    assert result.keywords == ["如何配置", "MiniMax-M3", "RAG"]


def test_process_copies_structured_filters_without_mutating_request() -> None:
    filters = {
        "collection": "docs",
        "doc_type": "pdf",
        "time_range": {"gte": "2026-01-01"},
    }
    request = QueryRequest(query="generation state machine", filters=filters)

    result = QueryProcessor().process(request)
    result.filters["language"] = "zh"

    assert result.filters["time_range"] == {"gte": "2026-01-01"}
    assert "language" not in request.filters
    assert request.filters == filters


def test_process_falls_back_when_query_contains_only_stop_words() -> None:
    result = QueryProcessor().process(QueryRequest(query="the AND is"))

    assert result.keywords == ["the", "AND", "is"]


def test_query_processor_matches_port_contract() -> None:
    assert isinstance(QueryProcessor(), QueryProcessorPort)
