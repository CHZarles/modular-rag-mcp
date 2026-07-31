"""KnowledgeService 的进程内实现。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.core.services.knowledge_service import KnowledgeCatalog
from src.core.types import CollectionInfo, DocumentSummary, JsonDict, QueryRequest, QueryResponse
from src.ports.ingestion import FileIntegrityStore
from src.ports.query import QueryEngine
from src.ports.response import ResponseBuilder


class LocalKnowledgeService:
    """组合本地查询引擎、响应构建器和知识目录。"""

    def __init__(
        self,
        query_engine: QueryEngine,
        response_builder: ResponseBuilder,
        catalog: KnowledgeCatalog,
    ) -> None:
        self.query_engine = query_engine
        self.response_builder = response_builder
        self.catalog = catalog

    def query(self, request: QueryRequest, trace: Any | None = None) -> QueryResponse:
        """执行完整检索链路，并转换为与入口类型无关的统一响应。"""
        candidates = self.query_engine.search(request, trace=trace)
        return self.response_builder.build(request, candidates, trace=trace)

    def list_collections(self) -> list[CollectionInfo]:
        """列出控制面中至少有一个已发布文档的集合。"""
        return self.catalog.list_collections()

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        """读取当前已发布文档的轻量摘要。"""
        return self.catalog.get_document_summary(doc_id)


class SQLiteKnowledgeCatalog:
    """从摄取控制面读取当前公开文档，不扫描 Chroma 的内部数据。

    控制面是 generation 可见性的权威来源。旧 generation 即使仍留在物理索引中，
    也不会出现在集合统计或文档摘要里。
    """

    def __init__(self, integrity_store: FileIntegrityStore) -> None:
        self.integrity_store = integrity_store

    def list_collections(self) -> list[CollectionInfo]:
        totals: dict[str, dict[str, int]] = {}
        for row in self._active_documents():
            collection = _required_row_text(row, "collection")
            counts = totals.setdefault(collection, {"documents": 0, "chunks": 0})
            counts["documents"] += 1
            counts["chunks"] += _nonnegative_row_int(row, "chunk_count")

        return [
            CollectionInfo(
                name=name,
                document_count=counts["documents"],
                chunk_count=counts["chunks"],
            )
            for name, counts in sorted(totals.items(), key=lambda item: item[0].casefold())
        ]

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        normalized_id = doc_id.strip()
        if not normalized_id:
            raise ValueError("doc_id must not be empty")

        for row in self._active_documents():
            # doc_key 是稳定逻辑 ID；同时接受当前内容哈希，兼容摄取结果中的 document_id。
            if normalized_id not in {str(row.get("doc_key", "")), str(row.get("file_hash", ""))}:
                continue
            source_path = _required_row_text(row, "file_path")
            return DocumentSummary(
                doc_id=_required_row_text(row, "doc_key"),
                source_path=source_path,
                title=Path(source_path).stem,
                metadata={
                    "collection": _required_row_text(row, "collection"),
                    "source_revision": _required_row_text(row, "file_hash"),
                    "generation": _nonnegative_row_int(row, "generation"),
                    "chunk_count": _nonnegative_row_int(row, "chunk_count"),
                    "processed_at": row.get("processed_at"),
                },
            )
        raise KeyError(f"document not found: {normalized_id}")

    def _active_documents(self) -> list[JsonDict]:
        active_generations = self.integrity_store.get_active_generations()
        return [
            row
            for row in self.integrity_store.list_processed()
            if row.get("attempt_status") == "published"
            and active_generations.get(str(row.get("doc_key", ""))) == row.get("generation")
        ]


def _required_row_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"invalid ingestion history field: {key}")
    return value


def _nonnegative_row_int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
