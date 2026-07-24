"""Dense encoder — embeds chunk text via the configured BaseEmbedding."""

from __future__ import annotations

from typing import Any

from src.core.types import Chunk
from src.ports.ingestion import BaseEmbedding


class DenseEncoder:
    """Compute dense vectors for a list of chunks."""

    def __init__(self, embedding: BaseEmbedding) -> None:
        self.embedding = embedding

    def encode(self, chunks: list[Chunk], trace: Any | None = None) -> list[list[float]]:
        texts = [c.text for c in chunks]
        if not texts:
            return []
        return self.embedding.embed(texts, trace=trace)