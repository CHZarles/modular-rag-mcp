"""get_document_summary Tool service-boundary and result-shape tests."""

from __future__ import annotations

import pytest

from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    JsonDict,
    QueryRequest,
    QueryResponse,
)
from src.mcp_server.tools import (
    GetDocumentSummaryTool,
    ToolArgumentError,
    ToolHandler,
)


class FakeKnowledgeService:
    def __init__(self, documents: dict[str, DocumentSummary]) -> None:
        self.documents = documents
        self.summary_requests: list[str] = []

    def query(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> QueryResponse:  # pragma: no cover
        raise AssertionError("query must not be called")

    def list_collections(self) -> list[CollectionInfo]:  # pragma: no cover
        raise AssertionError("list_collections must not be called")

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        self.summary_requests.append(doc_id)
        try:
            return self.documents[doc_id]
        except KeyError as exc:
            raise KeyError(f"document not found: {doc_id}") from exc


def test_tool_returns_readable_and_structured_document_summary() -> None:
    document = DocumentSummary(
        doc_id="doc-1",
        source_path="/knowledge/rag-guide.pdf",
        title="RAG Guide",
        summary="A practical guide to hybrid retrieval.",
        tags=["rag", "retrieval"],
        metadata={"collection": "docs", "chunk_count": 12},
    )
    service = FakeKnowledgeService({document.doc_id: document})
    tool = GetDocumentSummaryTool(lambda: service)

    result = tool.call({"doc_id": "  doc-1  "})

    assert isinstance(tool, ToolHandler)
    assert service.summary_requests == ["doc-1"]
    assert result["structuredContent"] == {"document": document.to_dict()}
    text = result["content"][0]["text"]
    assert "# RAG Guide" in text
    assert "A practical guide to hybrid retrieval." in text
    assert "rag、retrieval" in text
    assert "/knowledge/rag-guide.pdf" in text


def test_tool_formats_missing_optional_summary_fields() -> None:
    document = DocumentSummary(doc_id="doc-2", source_path="notes.txt")

    result = GetDocumentSummaryTool(
        lambda: FakeKnowledgeService({document.doc_id: document})
    ).call({"doc_id": "doc-2"})

    assert "# 未命名文档" in result["content"][0]["text"]
    assert "暂无摘要。" in result["content"][0]["text"]
    assert "暂无标签" in result["content"][0]["text"]


def test_missing_document_returns_expected_tool_error() -> None:
    service = FakeKnowledgeService({})

    result = GetDocumentSummaryTool(lambda: service).call({"doc_id": "missing"})

    assert result == {
        "content": [{"type": "text", "text": "未找到文档：`missing`。"}],
        "structuredContent": {
            "error": {"code": "document_not_found", "doc_id": "missing"}
        },
        "isError": True,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"doc_id": ""},
        {"doc_id": "   "},
        {"doc_id": 123},
        {"doc_id": "doc-1", "collection": "docs"},
    ],
)
def test_tool_rejects_invalid_arguments(arguments: JsonDict) -> None:
    tool = GetDocumentSummaryTool(lambda: FakeKnowledgeService({}))

    with pytest.raises(ToolArgumentError):
        tool.call(arguments)
