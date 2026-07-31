"""MCP、CLI 与 Dashboard 共用的知识服务契约。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import CollectionInfo, DocumentSummary, QueryRequest, QueryResponse


@runtime_checkable
class KnowledgeService(Protocol):
    """屏蔽检索和存储细节的稳定应用层入口。"""

    def query(self, request: QueryRequest, trace: Any | None = None) -> QueryResponse: ...
    def list_collections(self) -> list[CollectionInfo]: ...
    def get_document_summary(self, doc_id: str) -> DocumentSummary: ...


@runtime_checkable
class KnowledgeCatalog(Protocol):
    """LocalKnowledgeService 读取集合和文档摘要所需的最小目录能力。"""

    def list_collections(self) -> list[CollectionInfo]: ...
    def get_document_summary(self, doc_id: str) -> DocumentSummary: ...
