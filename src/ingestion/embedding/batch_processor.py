"""按稳定批次驱动摄取阶段的稠密与稀疏编码。"""

from __future__ import annotations

from contextlib import nullcontext

from src.core.trace import TraceContext
from src.core.types import Chunk, JsonDict
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder


class BatchProcessor:
    """限制单次编码规模，并保持两路编码结果与 Chunk 顺序一致。"""

    def __init__(
        self,
        dense_encoder: DenseEncoder | None,
        sparse_encoder: SparseEncoder,
        batch_size: int,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.dense_encoder = dense_encoder
        self.sparse_encoder = sparse_encoder
        self.batch_size = batch_size

    def process(
        self,
        chunks: list[Chunk],
        trace: TraceContext | None = None,
    ) -> tuple[list[list[float]], list[JsonDict]]:
        """分批编码并按输入顺序返回 ``(dense_vectors, sparse_vectors)``。"""
        dense_vectors: list[list[float]] = []
        sparse_vectors: list[JsonDict] = []
        batch_count = (len(chunks) + self.batch_size - 1) // self.batch_size

        for start in range(0, len(chunks), self.batch_size):
            batch = chunks[start : start + self.batch_size]
            batch_index = start // self.batch_size + 1
            timing = (
                trace.stage_timer(
                    "embedding_batch",
                    {
                        "batch_index": batch_index,
                        "batch_count": batch_count,
                        "chunk_count": len(batch),
                    },
                )
                if trace is not None
                else nullcontext()
            )
            with timing:
                dense_batch = (
                    self.dense_encoder.encode(batch, trace=trace)
                    if self.dense_encoder is not None
                    else []
                )
                sparse_batch = self.sparse_encoder.encode(batch, trace=trace)

            if self.dense_encoder is not None and len(dense_batch) != len(batch):
                raise ValueError("dense batch output count must match chunk count")
            if len(sparse_batch) != len(batch):
                raise ValueError("sparse batch output count must match chunk count")
            dense_vectors.extend(dense_batch)
            sparse_vectors.extend(sparse_batch)

        return dense_vectors, sparse_vectors


__all__ = ["BatchProcessor"]
