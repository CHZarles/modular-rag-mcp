"""本地 KnowledgeService 的配置装配与 SQLite 目录集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.core.services import (
    LocalKnowledgeService,
    SQLiteKnowledgeCatalog,
    build_knowledge_service,
    build_local_query_engine,
)
from src.core.settings import Settings
from src.core.types import ChunkRecord, ClaimHandle, JsonDict, QueryRequest, SearchHit
from src.libs.embedding import EmbeddingFactory
from src.libs.loader import SQLiteIntegrityStore
from src.libs.vector_store import VectorStoreFactory


class DeterministicEmbedding:
    def embed(
        self,
        texts: list[str],
        trace: object | None = None,
    ) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FixtureVectorStore:
    hits: list[SearchHit] = []

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        return None

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        return list(self.hits[:top_k])

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return []

    def delete_by_metadata(self, filters: JsonDict) -> int:
        return 0


@pytest.fixture(autouse=True)
def fake_backends() -> Iterator[None]:
    EmbeddingFactory.register("knowledge-test", lambda config: DeterministicEmbedding())
    FixtureVectorStore.hits = []
    VectorStoreFactory.register("knowledge-test", lambda config: FixtureVectorStore())
    yield
    EmbeddingFactory.unregister("knowledge-test")
    VectorStoreFactory.unregister("knowledge-test")
    FixtureVectorStore.hits = []


def test_default_local_factory_builds_query_response_from_config(tmp_path: Path) -> None:
    store = SQLiteIntegrityStore(tmp_path / "db/history.db")
    source = tmp_path / "manual.pdf"
    source.write_text("fixture", encoding="utf-8")
    claim = _publish(store, source, "docs", "revision-1", chunks=1)
    FixtureVectorStore.hits = [
        SearchHit(
            id="chunk-1",
            text="A generation token fences stale workers.",
            metadata={
                "source_path": str(source.resolve()),
                "collection": "docs",
                "doc_key": claim.doc_key,
                "generation": claim.generation,
                "page": 2,
            },
            score=0.1,
            score_kind="distance",
        )
    ]
    service = build_knowledge_service(_settings(tmp_path))

    assert isinstance(service, LocalKnowledgeService)
    response = service.query(
        QueryRequest(query="lease generation", collection="docs", request_id="req-1")
    )

    assert "A generation token fences stale workers." in response.answer
    assert response.items[0].chunk_id == "chunk-1"
    assert response.items[0].source == "fusion"
    assert response.citations[0].page == 2
    assert response.request_id == "req-1"
    assert response.metadata == {
        "collection": "docs",
        "candidate_count": 1,
        "image_count": 0,
    }
    assert service.list_collections()[0].name == "docs"


def test_sqlite_catalog_only_exposes_active_generations(tmp_path: Path) -> None:
    store = SQLiteIntegrityStore(tmp_path / "db/history.db")
    docs_a = tmp_path / "a.pdf"
    docs_b = tmp_path / "b.pdf"
    docs_a.write_text("a", encoding="utf-8")
    docs_b.write_text("b", encoding="utf-8")

    active_a = _publish(store, docs_a, "docs", "revision-a", chunks=3)
    active_b = _publish(store, docs_b, "notes", "revision-b", chunks=2)
    # 新一代失败后，上一代仍是唯一可见版本。
    failed = store.try_claim("revision-a2", str(docs_a), "docs", "worker-c", 60)
    assert failed.handle is not None
    store.mark_failed(failed.handle, "fixture failure")

    catalog = SQLiteKnowledgeCatalog(store)

    assert [
        (item.name, item.document_count, item.chunk_count) for item in catalog.list_collections()
    ] == [
        ("docs", 1, 3),
        ("notes", 1, 2),
    ]
    summary = catalog.get_document_summary(active_a.doc_key)
    assert summary.source_path == str(docs_a.resolve())
    assert summary.title == "a"
    assert summary.metadata["generation"] == active_a.generation
    # 当前内容哈希也是兼容查询 ID，但响应始终返回稳定 doc_key。
    assert catalog.get_document_summary("revision-b").doc_id == active_b.doc_key
    with pytest.raises(KeyError, match="document not found"):
        catalog.get_document_summary("missing")


def test_local_factory_can_build_sparse_only_without_dense_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    settings.retrieval["enable_dense"] = False
    settings.retrieval["enable_sparse"] = True
    monkeypatch.setattr(
        "src.core.services.knowledge_service_factory.create_embedding",
        lambda settings: pytest.fail("disabled dense route must not create an embedding"),
    )
    monkeypatch.setattr(
        "src.core.services.knowledge_service_factory.create_vector_store",
        lambda settings: pytest.fail("disabled dense route must not create a vector store"),
    )

    engine = build_local_query_engine(settings)

    assert engine.config.enable_dense is False
    assert engine.config.enable_sparse is True
    assert engine.dense_retriever is None
    assert engine.sparse_retriever is not None


def test_local_factory_rejects_disabling_every_retrieval_route(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.retrieval["enable_dense"] = False
    settings.retrieval["enable_sparse"] = False

    with pytest.raises(ValueError, match="at least one retrieval route"):
        build_local_query_engine(settings)


def _publish(
    store: SQLiteIntegrityStore,
    source: Path,
    collection: str,
    revision: str,
    *,
    chunks: int,
) -> ClaimHandle:
    result = store.try_claim(revision, str(source), collection, revision, 60)
    assert result.handle is not None
    store.mark_staged(result.handle)
    store.publish(result.handle, chunks)
    return result.handle


def _settings(root: Path) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "knowledge-test"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "knowledge-test"},
        retrieval={
            "sparse_backend": "bm25",
            "fusion_algorithm": "rrf",
            "top_k_dense": 7,
            "top_k_sparse": 8,
            "top_k_final": 9,
        },
        rerank={"backend": "none", "top_m": 30, "timeout_seconds": 10},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={
            "storage": {
                "integrity_db_path": str(root / "db/history.db"),
                "bm25_path": str(root / "db/bm25"),
            }
        },
    )
