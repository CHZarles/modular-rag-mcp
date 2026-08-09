from __future__ import annotations

from typing import Any

import pytest

from core.trace import TraceContext
from core.types import Chunk
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder


class RecordingEmbedding:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Any | None]] = []

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        self.calls.append((list(texts), trace))
        return [[float(text.removeprefix("chunk-"))] for text in texts]


class RecordingSparseEncoder(SparseEncoder):
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Any | None]] = []

    def encode(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(([chunk.id for chunk in chunks], trace))
        return [{"terms": {chunk.text: 1}, "doc_length": 1} for chunk in chunks]


class DroppingSparseEncoder(SparseEncoder):
    def encode(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[dict[str, Any]]:
        return []


def make_chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            id=f"chunk-{index}",
            text=f"chunk-{index}",
            metadata={},
            source_ref="document",
            chunk_index=index,
        )
        for index in range(count)
    ]


def test_process_splits_five_chunks_into_three_ordered_batches() -> None:
    embedding = RecordingEmbedding()
    sparse_encoder = RecordingSparseEncoder()
    trace = TraceContext(trace_type="ingestion")
    processor = BatchProcessor(
        dense_encoder=DenseEncoder(embedding=embedding),
        sparse_encoder=sparse_encoder,
        batch_size=2,
    )

    dense_vectors, sparse_vectors = processor.process(make_chunks(5), trace=trace)

    assert [texts for texts, _ in embedding.calls] == [
        ["chunk-0", "chunk-1"],
        ["chunk-2", "chunk-3"],
        ["chunk-4"],
    ]
    assert [ids for ids, _ in sparse_encoder.calls] == [
        ["chunk-0", "chunk-1"],
        ["chunk-2", "chunk-3"],
        ["chunk-4"],
    ]
    assert dense_vectors == [[0.0], [1.0], [2.0], [3.0], [4.0]]
    assert [item["terms"] for item in sparse_vectors] == [
        {f"chunk-{index}": 1} for index in range(5)
    ]
    assert all(call_trace is trace for _, call_trace in embedding.calls)
    assert all(call_trace is trace for _, call_trace in sparse_encoder.calls)

    batch_stages = [stage for stage in trace.stages if stage["stage"] == "embedding_batch"]
    assert [stage["data"] for stage in batch_stages] == [
        {"batch_index": 1, "batch_count": 3, "chunk_count": 2},
        {"batch_index": 2, "batch_count": 3, "chunk_count": 2},
        {"batch_index": 3, "batch_count": 3, "chunk_count": 1},
    ]
    assert all(stage["elapsed_ms"] >= 0 for stage in batch_stages)


def test_process_empty_input_skips_both_encoders() -> None:
    embedding = RecordingEmbedding()
    sparse_encoder = RecordingSparseEncoder()
    processor = BatchProcessor(
        dense_encoder=DenseEncoder(embedding=embedding),
        sparse_encoder=sparse_encoder,
        batch_size=2,
    )

    assert processor.process([]) == ([], [])
    assert embedding.calls == []
    assert sparse_encoder.calls == []


def test_process_skips_dense_encoding_when_disabled() -> None:
    sparse_encoder = RecordingSparseEncoder()
    processor = BatchProcessor(
        dense_encoder=None,
        sparse_encoder=sparse_encoder,
        batch_size=2,
    )

    dense_vectors, sparse_vectors = processor.process(make_chunks(3))

    assert dense_vectors == []
    assert len(sparse_vectors) == 3
    assert [ids for ids, _ in sparse_encoder.calls] == [
        ["chunk-0", "chunk-1"],
        ["chunk-2"],
    ]


def test_process_rejects_sparse_results_that_break_chunk_alignment() -> None:
    processor = BatchProcessor(
        dense_encoder=DenseEncoder(embedding=RecordingEmbedding()),
        sparse_encoder=DroppingSparseEncoder(),
        batch_size=2,
    )

    with pytest.raises(ValueError, match="sparse batch output count must match chunk count"):
        processor.process(make_chunks(2))


@pytest.mark.parametrize("batch_size", [0, -1])
def test_constructor_rejects_non_positive_batch_size(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        BatchProcessor(
            dense_encoder=DenseEncoder(embedding=RecordingEmbedding()),
            sparse_encoder=RecordingSparseEncoder(),
            batch_size=batch_size,
        )
