"""把领域 Chunk 批量转换为稠密语义向量。"""

from __future__ import annotations

from typing import Any

from src.core.types import Chunk
from src.libs.embedding import BaseEmbedding, create_embedding


class DenseEncoder:
    """连接摄取领域对象与供应商无关的 Embedding 端口。"""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        embedding: BaseEmbedding | None = None,
    ) -> None:
        self.embedding = embedding if embedding is not None else create_embedding(settings)

    def encode(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[list[float]]:
        """按 Chunk 顺序批量编码，并校验数量与向量维度契约。"""
        if not chunks:
            return []

        vectors = self.embedding.embed([chunk.text for chunk in chunks], trace=trace)
        if len(vectors) != len(chunks):
            raise ValueError("dense encoder vector count must match chunk count")

        dimension = len(vectors[0])
        if dimension == 0 or any(len(vector) != dimension for vector in vectors):
            raise ValueError("dense encoder vectors must have the same non-zero dimension")
        return vectors


__all__ = ["DenseEncoder"]
