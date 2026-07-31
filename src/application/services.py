"""应用服务层。

MCP Tool、CLI 和 Dashboard 等入口适配器只依赖这里的契约，不直接依赖具体的
检索、存储或模型供应商实现，从而保持入口层与基础设施解耦。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from src.core.services import KnowledgeService, LocalKnowledgeService
from src.core.trace import TraceContext
from src.core.types import (
    CollectionInfo,
    DeleteResult,
    DocumentSummary,
    EvaluationCase,
    EvaluationReport,
    IngestionRequest,
    IngestionResult,
    JsonDict,
)
from src.ports.observability import BaseTracer
from src.ports.query import QueryEngine


@runtime_checkable
class IngestionService(Protocol):
    """接收文档摄取请求并返回统一结果。"""

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult: ...


@runtime_checkable
class DocumentService(Protocol):
    """管理文档详情、集合统计与删除操作。"""

    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]: ...
    def get_document_detail(self, doc_id: str) -> JsonDict: ...
    def delete_document(self, source_path: str, collection: str) -> DeleteResult: ...
    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo: ...


@runtime_checkable
class EvaluationService(Protocol):
    """执行一组评估用例并汇总指标。"""

    def run_evaluation(
        self,
        cases: list[EvaluationCase],
        evaluator_names: list[str] | None = None,
    ) -> EvaluationReport: ...


@runtime_checkable
class ComponentRegistry(Protocol):
    """集中装配应用依赖，避免入口层自行构造组件。"""

    def build_knowledge_service(self) -> KnowledgeService: ...
    def build_ingestion_service(self) -> IngestionService: ...
    def build_document_service(self) -> DocumentService: ...
    def build_evaluation_service(self) -> EvaluationService: ...
    def build_query_engine(self) -> QueryEngine: ...
    def build_tracer(self) -> BaseTracer: ...


class LocalIngestionService:
    """隔离 CLI、Dashboard 与摄取流水线内部细节的轻量封装。"""

    def __init__(self, pipeline: IngestionService) -> None:
        self.pipeline = pipeline

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        return self.pipeline.ingest(request, on_progress=on_progress, trace=trace)


__all__ = [
    "ComponentRegistry",
    "DocumentService",
    "EvaluationService",
    "IngestionService",
    "KnowledgeService",
    "LocalIngestionService",
    "LocalKnowledgeService",
]
