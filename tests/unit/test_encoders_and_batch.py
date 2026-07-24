"""Tests for C8 + C9 + C10: DenseEncoder, SparseEncoder, BatchProcessor."""

from __future__ import annotations

import pytest

from src.core.types import Chunk
from src.ingestion.embedding.batch_processor import BatchProcessor
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ports.ingestion import BaseEmbedding


def _chunk(text: str, cid: str = "c") -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata={"source_path": "x.txt"},
        source_ref="d",
        chunk_index=0,
    )


# ---------------------------------------------------------------------------
# DenseEncoder
# ---------------------------------------------------------------------------


def test_dense_encoder_returns_vectors_per_chunk() -> None:
    embed = FakeEmbedding([[0.1, 0.2], [0.3, 0.4]])
    vectors = DenseEncoder(embed).encode([_chunk("a"), _chunk("b")])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_dense_encoder_empty_chunks() -> None:
    assert DenseEncoder(FakeEmbedding([])).encode([]) == []


# ---------------------------------------------------------------------------
# SparseEncoder
# ---------------------------------------------------------------------------


def test_sparse_encoder_counts_terms() -> None:
    chunks = [_chunk("the the cat sat on the mat")]
    out = SparseEncoder().encode(chunks)
    terms = out[0]["terms"]
    # Stopword "the" is removed.
    assert terms.get("cat") == 1
    assert "the" not in terms
    assert terms.get("mat") == 1


def test_sparse_encoder_empty_text() -> None:
    chunks = [_chunk("")]
    out = SparseEncoder().encode(chunks)
    assert out[0]["terms"] == {}


# ---------------------------------------------------------------------------
# BatchProcessor
# ---------------------------------------------------------------------------


def test_batch_processor_chunks_into_batches() -> None:
    batcher = BatchProcessor(batch_size=3)
    seen: list[list[int]] = []

    def process_fn(batch: list[int]) -> list[str]:
        seen.append(batch)
        return [str(x) for x in batch]

    out = batcher.process(range(7), process_fn)
    assert seen == [[0, 1, 2], [3, 4, 5], [6]]
    assert out == ["0", "1", "2", "3", "4", "5", "6"]


def test_batch_processor_exact_multiple() -> None:
    batcher = BatchProcessor(batch_size=2)
    out = batcher.process([1, 2, 3, 4], lambda b: [sum(b)])
    assert out == [3, 7]


def test_batch_processor_rejects_zero_batch_size() -> None:
    with pytest.raises(ValueError):
        BatchProcessor(batch_size=0)


def test_batch_processor_empty_input() -> None:
    assert BatchProcessor().process([], lambda b: b) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeEmbedding(BaseEmbedding):
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = list(vectors)
        self.calls: list[list[str]] = []

    def embed(self, texts, trace=None):
        self.calls.append(list(texts))
        # Return one vector per requested text.
        return [list(v) for v in self.vectors[: len(texts)]]