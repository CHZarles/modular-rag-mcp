"""list_collections Tool service-boundary and result-shape tests."""

from __future__ import annotations

import pytest

from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    JsonDict,
    QueryRequest,
    QueryResponse,
)
from src.mcp_server.tools import ListCollectionsTool, ToolArgumentError, ToolHandler


class FakeKnowledgeService:
    def __init__(self, collections: list[CollectionInfo]) -> None:
        self.collections = collections
        self.list_calls = 0

    def query(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> QueryResponse:  # pragma: no cover
        raise AssertionError("query must not be called")

    def list_collections(self) -> list[CollectionInfo]:
        self.list_calls += 1
        return self.collections

    def get_document_summary(
        self,
        doc_id: str,
    ) -> DocumentSummary:  # pragma: no cover
        raise AssertionError("get_document_summary must not be called")


def test_tool_lists_collection_statistics_from_knowledge_service() -> None:
    service = FakeKnowledgeService(
        [
            CollectionInfo(
                name="docs",
                document_count=2,
                chunk_count=12,
                image_count=3,
                metadata={"description": "Engineering documents"},
            ),
            CollectionInfo(name="notes", document_count=1, chunk_count=4),
        ]
    )
    tool = ListCollectionsTool(lambda: service)

    result = tool.call({})

    assert isinstance(tool, ToolHandler)
    assert service.list_calls == 1
    assert result["structuredContent"] == {
        "collections": [collection.to_dict() for collection in service.collections],
        "total": 2,
    }
    text = result["content"][0]["text"]
    assert "`docs`：2 个文档，12 个片段，3 张图片" in text
    assert "Engineering documents" in text
    assert "`notes`：1 个文档，4 个片段，0 张图片" in text


def test_tool_returns_friendly_empty_result() -> None:
    result = ListCollectionsTool(lambda: FakeKnowledgeService([])).call({})

    assert result == {
        "content": [{"type": "text", "text": "当前没有可用的知识集合。"}],
        "structuredContent": {"collections": [], "total": 0},
    }


@pytest.mark.parametrize("arguments", [{"collection": "docs"}, {"unknown": 1}])
def test_tool_rejects_all_arguments(arguments: JsonDict) -> None:
    tool = ListCollectionsTool(lambda: FakeKnowledgeService([]))

    with pytest.raises(ToolArgumentError, match="unsupported arguments"):
        tool.call(arguments)
