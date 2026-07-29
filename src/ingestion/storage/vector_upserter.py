"""把最终 Chunk 与稠密向量组装为可幂等写入的向量记录。"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from src.core.types import Chunk, ChunkRecord, JsonDict
from src.ports.ingestion import BaseVectorStore


class VectorUpserter:
    """生成最终存储 ID，并把有序 ChunkRecord 批量交给 VectorStore。"""

    def __init__(self, vector_store: BaseVectorStore) -> None:
        self.vector_store = vector_store

    def upsert(
        self,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[JsonDict] | None = None,
        trace: Any | None = None,
    ) -> list[ChunkRecord]:
        """校验三路数据对齐，幂等写入并返回保持输入顺序的记录。"""
        if len(dense_vectors) != len(chunks):
            raise ValueError("vector upsert error: dense vector count must match chunk count")
        if sparse_vectors is not None and len(sparse_vectors) != len(chunks):
            raise ValueError("vector upsert error: sparse vector count must match chunk count")
        if not chunks:
            return []

        records: list[ChunkRecord] = []
        seen_ids: set[str] = set()
        dimension: int | None = None
        for index, (chunk, dense_vector) in enumerate(
            zip(chunks, dense_vectors, strict=True)
        ):
            _validate_chunk(chunk)
            _validate_vector(dense_vector, chunk.id)
            if dimension is None:
                dimension = len(dense_vector)
            elif len(dense_vector) != dimension:
                raise ValueError("vector upsert error: dense vectors must have the same dimension")

            # C4 的 ID 对应切分时正文；这里的存储 ID 反映 C5/C7 处理后的最终正文。
            content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            record_id = _storage_id(chunk, content_hash)
            if record_id in seen_ids:
                raise ValueError(f"vector upsert error: duplicate storage id {record_id!r}")
            seen_ids.add(record_id)
            sparse_vector = sparse_vectors[index] if sparse_vectors is not None else None
            records.append(
                ChunkRecord(
                    id=record_id,
                    text=chunk.text,
                    metadata=dict(chunk.metadata),
                    dense_vector=list(dense_vector),
                    sparse_vector=dict(sparse_vector) if sparse_vector is not None else None,
                    content_hash=content_hash,
                )
            )

        # VectorStore 的 upsert 语义负责用相同 ID 覆盖旧值；这里只调用一次以保留批次边界。
        self.vector_store.upsert(records, trace=trace)
        return records


def _validate_chunk(chunk: Chunk) -> None:
    source_path = chunk.metadata.get("source_path")
    if not isinstance(source_path, str) or not source_path.strip():
        raise ValueError("vector upsert error: source_path must be non-empty")
    if chunk.chunk_index < 0:
        raise ValueError("vector upsert error: chunk_index must be non-negative")


def _validate_vector(vector: list[float], chunk_id: str) -> None:
    if not vector:
        raise ValueError(f"vector upsert error: chunk {chunk_id!r} dense vector must be non-empty")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError(f"vector upsert error: chunk {chunk_id!r} dense vector must be finite")


def _storage_id(chunk: Chunk, content_hash: str) -> str:
    source_path = str(chunk.metadata["source_path"])
    identity = f"{source_path}\0{chunk.chunk_index}\0{content_hash[:8]}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


__all__ = ["VectorUpserter"]
