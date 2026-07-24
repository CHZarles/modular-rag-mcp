"""Chunk Refiner — a no-op default transform that preserves chunk text.

The full LLM-driven refinement is added in a follow-up; this default keeps
the pipeline end-to-end runnable and serves as the contract surface that
Transforms must satisfy.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.types import Chunk
from src.ports.ingestion import BaseTransform

_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class ChunkRefiner(BaseTransform):
    """Lightweight text cleanup: collapse runs of spaces, limit blank lines."""

    name = "chunk_refiner"

    def transform(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[Chunk]:
        return [
            Chunk(
                id=c.id,
                text=_clean(c.text),
                metadata=c.metadata,
                source_ref=c.source_ref,
                chunk_index=c.chunk_index,
                start_offset=c.start_offset,
                end_offset=c.end_offset,
            )
            for c in chunks
        ]


def _clean(text: str) -> str:
    if not text:
        return text
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()