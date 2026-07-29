from __future__ import annotations

from typing import Any

import pytest

from core.types import Chunk
from ingestion.embedding import DenseEncoder


class FakeEmbedding:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[list[str], Any | None]] = []

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        self.calls.append((texts, trace))
        return self.vectors


def make_chunks() -> list[Chunk]:
    return [
        Chunk(
            id="chunk-1",
            text="Dense retrieval captures semantic similarity.",
            metadata={},
            source_ref="document",
            chunk_index=0,
        ),
        Chunk(
            id="chunk-2",
            text="BM25 captures exact keyword matches.",
            metadata={},
            source_ref="document",
            chunk_index=1,
        ),
    ]


def test_encode_sends_chunk_texts_as_one_batch_and_preserves_order() -> None:
    chunks = make_chunks()
    expected = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    embedding = FakeEmbedding(expected)
    trace = object()

    result = DenseEncoder(embedding=embedding).encode(chunks, trace=trace)

    assert result == expected
    assert embedding.calls == [([chunk.text for chunk in chunks], trace)]


def test_encode_empty_batch_does_not_call_embedding() -> None:
    embedding = FakeEmbedding([])

    assert DenseEncoder(embedding=embedding).encode([]) == []
    assert embedding.calls == []


def test_encode_rejects_vector_count_mismatch() -> None:
    encoder = DenseEncoder(embedding=FakeEmbedding([[0.1, 0.2]]))

    with pytest.raises(ValueError, match="vector count must match chunk count"):
        encoder.encode(make_chunks())


@pytest.mark.parametrize(
    "vectors",
    [
        [[0.1, 0.2], [0.3]],
        [[], []],
    ],
    ids=["inconsistent-dimensions", "empty-vectors"],
)
def test_encode_rejects_invalid_vector_dimensions(vectors: list[list[float]]) -> None:
    encoder = DenseEncoder(embedding=FakeEmbedding(vectors))

    with pytest.raises(ValueError, match="same non-zero dimension"):
        encoder.encode(make_chunks())


def test_constructor_creates_embedding_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    embedding = FakeEmbedding([[0.1, 0.2], [0.3, 0.4]])
    received: list[object] = []

    def fake_create(settings: object) -> FakeEmbedding:
        received.append(settings)
        return embedding

    monkeypatch.setattr("src.ingestion.embedding.dense_encoder.create_embedding", fake_create)
    config = {"embedding": {"provider": "fake"}}

    result = DenseEncoder(config).encode(make_chunks())

    assert result == embedding.vectors
    assert received == [config]
