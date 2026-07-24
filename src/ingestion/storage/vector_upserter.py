"""Vector upserter — adapts ChunkRecord upserts for the ingestion pipeline."""

from __future__ import annotations

from typing import Any

from src.core.types import Chunk, ChunkRecord
from src.ports.ingestion import BaseVectorStore


class VectorUpserter:
    """Build ChunkRecords (chunk + dense + sparse vectors) and push them."""

    def __init__(self, vector_store: BaseVectorStore) -> None:
        self.vector_store = vector_store

    def upsert(
        self,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[dict[str, Any]] | None = None,
        trace: Any | None = None,
    ) -> None:
        records = [
            ChunkRecord.from_chunk(
                chunk=chunk,
                dense_vector=dense_vectors[i] if i < len(dense_vectors) else None,
                sparse_vector=sparse_vectors[i] if sparse_vectors and i < len(sparse_vectors) else None,
                content_hash=chunk.id,
            )
            for i, chunk in enumerate(chunks)
        ]
        self.vector_store.upsert(records, trace=trace)