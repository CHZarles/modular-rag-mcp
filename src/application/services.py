"""Application service layer.

Entry adapters such as MCP tools, CLI commands, and dashboards should depend on
these contracts instead of concrete retrieval, storage, or provider classes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

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
    QueryRequest,
    QueryResponse,
)
from src.ports.observability import BaseTracer
from src.ports.query import QueryEngine
from src.ports.response import ResponseBuilder


@runtime_checkable
class KnowledgeService(Protocol):
    def query(self, request: QueryRequest, trace: TraceContext | None = None) -> QueryResponse: ...
    def list_collections(self) -> list[CollectionInfo]: ...
    def get_document_summary(self, doc_id: str) -> DocumentSummary: ...


@runtime_checkable
class IngestionService(Protocol):
    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult: ...


@runtime_checkable
class DocumentService(Protocol):
    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]: ...
    def get_document_detail(self, doc_id: str) -> JsonDict: ...
    def delete_document(self, source_path: str, collection: str) -> DeleteResult: ...
    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo: ...


@runtime_checkable
class EvaluationService(Protocol):
    def run_evaluation(
        self,
        cases: list[EvaluationCase],
        evaluator_names: list[str] | None = None,
    ) -> EvaluationReport: ...


@runtime_checkable
class ComponentRegistry(Protocol):
    def build_knowledge_service(self) -> KnowledgeService: ...
    def build_ingestion_service(self) -> IngestionService: ...
    def build_document_service(self) -> DocumentService: ...
    def build_evaluation_service(self) -> EvaluationService: ...
    def build_query_engine(self) -> QueryEngine: ...
    def build_tracer(self) -> BaseTracer: ...


class LocalKnowledgeService:
    """Thin in-process implementation for entry adapters.

    It deliberately composes only high-level query/response services. Storage
    details stay behind ``DocumentService`` and retriever ports.
    """

    def __init__(
        self,
        query_engine: QueryEngine,
        response_builder: ResponseBuilder,
        document_service: DocumentService,
    ) -> None:
        self.query_engine = query_engine
        self.response_builder = response_builder
        self.document_service = document_service

    def query(self, request: QueryRequest, trace: TraceContext | None = None) -> QueryResponse:
        candidates = self.query_engine.search(request, trace=trace)
        return self.response_builder.build(request, candidates, trace=trace)

    def list_collections(self) -> list[CollectionInfo]:
        return [self.document_service.get_collection_stats(None)]

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        detail = self.document_service.get_document_detail(doc_id)
        return DocumentSummary(
            doc_id=doc_id,
            source_path=str(detail.get("source_path", "")),
            title=detail.get("title"),
            summary=detail.get("summary"),
            tags=list(detail.get("tags", [])),
            metadata=dict(detail.get("metadata", {})),
        )


class LocalIngestionService:
    """Thin wrapper that keeps CLI/Dashboard away from pipeline internals."""

    def __init__(self, pipeline: IngestionService) -> None:
        self.pipeline = pipeline

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        return self.pipeline.ingest(request, on_progress=on_progress, trace=trace)
