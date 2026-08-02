"""DenseRetriever 的向量化编排与结果转换测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.query_engine import DenseRetriever
from src.core.types import (
    ChunkRecord,
    JsonDict,
    RetrievalCandidate,
    RetrievalResult,
    SearchHit,
)
from src.ports.query import DenseRetriever as DenseRetrieverPort


class FakeEmbedding:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[list[str], object | None]] = []

    def embed(
        self,
        texts: list[str],
        trace: object | None = None,
    ) -> list[list[float]]:
        self.calls.append((texts, trace))
        return self.vectors


class PurposeAwareEmbedding(FakeEmbedding):
    def __init__(self, vector: list[float]) -> None:
        super().__init__([[9.9]])
        self.vector = vector
        self.query_calls: list[tuple[str, object | None]] = []

    def embed_query(self, text: str, trace: object | None = None) -> list[float]:
        self.query_calls.append((text, trace))
        return self.vector


class FakeVectorStore:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[list[float], int, JsonDict | None, object | None]] = []

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        return None

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        self.calls.append((vector, top_k, filters, trace))
        return self.hits

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return []

    def delete_by_metadata(self, filters: JsonDict) -> int:
        return 0


def test_retrieve_embeds_once_and_converts_vector_store_hits() -> None:
    trace = object()
    filters = {"collection": "docs", "doc_type": "pdf"}
    embedding = FakeEmbedding([[0.1, 0.2]])
    first_metadata = {"collection": "docs", "page": 3}
    store = FakeVectorStore(
        [
            SearchHit(
                id="chunk-1",
                text="MiniMax uses an OpenAI-compatible interface.",
                metadata=first_metadata,
                score=0.08,
                score_kind="distance",
                raw={"distance_metric": "cosine"},
            ),
            SearchHit(
                id="chunk-2",
                text="BM25 provides sparse retrieval.",
                metadata={"collection": "docs", "page": 7},
                score=0.21,
                score_kind="distance",
            ),
        ]
    )

    results = DenseRetriever(embedding, store).retrieve(
        "How does MiniMax integrate?",
        top_k=1,
        filters=filters,
        trace=trace,
    )

    assert embedding.calls == [(["How does MiniMax integrate?"], trace)]
    assert store.calls == [([0.1, 0.2], 1, filters, trace)]
    assert results == [
        RetrievalCandidate(
            chunk_id="chunk-1",
            text="MiniMax uses an OpenAI-compatible interface.",
            metadata={"collection": "docs", "page": 3},
            score=0.08,
            source="dense",
            rank=1,
            debug={
                "score_kind": "distance",
                "raw": {"distance_metric": "cosine"},
            },
        )
    ]
    assert isinstance(results[0], RetrievalResult)
    results[0].metadata["page"] = 99
    result_raw = results[0].debug["raw"]
    assert isinstance(result_raw, dict)
    result_raw["distance_metric"] = "changed"
    assert first_metadata["page"] == 3
    assert store.hits[0].raw == {"distance_metric": "cosine"}


def test_retrieve_blank_query_skips_external_dependencies() -> None:
    embedding = FakeEmbedding([[0.1, 0.2]])
    store = FakeVectorStore([])

    assert DenseRetriever(embedding, store).retrieve("  ", top_k=3) == []
    assert embedding.calls == []
    assert store.calls == []


def test_retrieve_prefers_provider_specific_query_embedding() -> None:
    trace = object()
    embedding = PurposeAwareEmbedding([0.7, 0.8])
    store = FakeVectorStore([])

    DenseRetriever(embedding, store).retrieve("question", top_k=2, trace=trace)

    assert embedding.query_calls == [("question", trace)]
    assert embedding.calls == []
    assert store.calls == [([0.7, 0.8], 2, None, trace)]


@pytest.mark.parametrize("top_k", [0, -1])
def test_retrieve_rejects_non_positive_top_k(top_k: int) -> None:
    retriever = DenseRetriever(FakeEmbedding([[0.1]]), FakeVectorStore([]))

    with pytest.raises(ValueError, match="top_k must be positive"):
        retriever.retrieve("query", top_k=top_k)


@pytest.mark.parametrize("vectors", [[], [[0.1], [0.2]]])
def test_retrieve_requires_exactly_one_query_embedding(
    vectors: list[list[float]],
) -> None:
    retriever = DenseRetriever(FakeEmbedding(vectors), FakeVectorStore([]))

    with pytest.raises(ValueError, match="embedding count must equal one"):
        retriever.retrieve("query", top_k=1)


def test_retrieve_rejects_empty_query_embedding() -> None:
    retriever = DenseRetriever(FakeEmbedding([[]]), FakeVectorStore([]))

    with pytest.raises(ValueError, match="query embedding must not be empty"):
        retriever.retrieve("query", top_k=1)


def test_retrieval_result_round_trip_and_port_contract() -> None:
    result = RetrievalResult(
        chunk_id="chunk-1",
        text="body",
        metadata={"collection": "docs"},
        score=0.5,
    )

    assert RetrievalResult.from_dict(result.to_dict()) == result
    assert isinstance(
        DenseRetriever(FakeEmbedding([[0.1]]), FakeVectorStore([])),
        DenseRetrieverPort,
    )
