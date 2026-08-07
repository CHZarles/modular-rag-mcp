"""KnowledgeService 稳定契约与依赖注入测试。"""

from __future__ import annotations

import json

import pytest

from src.core.services import (
    KnowledgeService,
    KnowledgeServiceDependencies,
    LocalKnowledgeService,
    build_knowledge_service,
)
from src.core.settings import Settings
from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)


class FakeQueryEngine:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates
        self.calls: list[QueryRequest] = []

    def search(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append(request)
        return self.candidates


class FakeResponseBuilder:
    def build(
        self,
        request: QueryRequest,
        candidates: list[RetrievalCandidate],
        trace: object | None = None,
    ) -> QueryResponse:
        return QueryResponse(
            results=candidates,
            request_id=request.request_id,
            metadata={"collection": request.collection},
        )


class FakeCatalog:
    def list_collections(self) -> list[CollectionInfo]:
        return [CollectionInfo(name="docs", document_count=1, chunk_count=2)]

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        return DocumentSummary(doc_id=doc_id, source_path="manual.pdf", title="manual")


def test_fake_can_replace_knowledge_service_without_real_backends() -> None:
    service = _build_injected_service()

    assert isinstance(service, KnowledgeService)
    assert service.list_collections()[0].name == "docs"
    assert service.get_document_summary("doc-1").title == "manual"

    response = service.query(
        QueryRequest(
            query="generation fence",
            top_k=1,
            collection="docs",
            filters={"kind": "guide"},
            include_images=False,
            request_id="req-1",
        )
    )

    assert response.results[0].chunk_id == "chunk-1"
    assert response.request_id == "req-1"
    # to_dict 的结果能直接交给 JSON/MCP/HTTP 边界，不泄漏 dataclass 实例。
    assert json.loads(json.dumps(response.to_dict(), ensure_ascii=False))["results"][0][
        "chunk_id"
    ] == "chunk-1"


def test_factory_rejects_unimplemented_mode_before_building_dependencies() -> None:
    with pytest.raises(ValueError, match="Unsupported knowledge service mode: http"):
        build_knowledge_service(
            _settings(mode="http"),
            KnowledgeServiceDependencies(
                query_engine=FakeQueryEngine([]),
                response_builder=FakeResponseBuilder(),
                catalog=FakeCatalog(),
            ),
        )


def _build_injected_service() -> LocalKnowledgeService:
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="A generation token fences stale workers.",
        metadata={"source_path": "manual.pdf", "collection": "docs"},
        score=0.9,
        source="fusion",
        rank=1,
    )
    service = build_knowledge_service(
        _settings(),
        KnowledgeServiceDependencies(
            query_engine=FakeQueryEngine([candidate]),
            response_builder=FakeResponseBuilder(),
            catalog=FakeCatalog(),
        ),
    )
    assert isinstance(service, LocalKnowledgeService)
    return service


def _settings(*, mode: str = "local") -> Settings:
    return Settings(
        knowledge_service={"mode": mode},
        llm={"provider": "openai"},
        embedding={"provider": "unused"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "unused"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
    )
