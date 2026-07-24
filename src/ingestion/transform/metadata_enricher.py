"""Metadata Enricher — adds Title/Summary/Tags heuristics (no LLM)."""

from __future__ import annotations

import re
from typing import Any

from src.core.types import Chunk

_HEADING = re.compile(r"^(#+)\s+(.+)$", re.MULTILINE)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your",
    "you", "are", "but", "not", "have", "has", "had", "was", "were",
    "they", "their", "them", "its", "our", "out", "any", "all",
}


class MetadataEnricher:
    """Heuristic enricher; the LLM-backed version is pluggable later."""

    name = "metadata_enricher"

    def transform(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[Chunk]:
        out: list[Chunk] = []
        for chunk in chunks:
            metadata = dict(chunk.metadata)
            metadata.setdefault("title", _extract_title(chunk.text))
            metadata.setdefault("summary", _extract_summary(chunk.text))
            metadata.setdefault("tags", _extract_tags(chunk.text))
            out.append(
                Chunk(
                    id=chunk.id,
                    text=chunk.text,
                    metadata=metadata,
                    source_ref=chunk.source_ref,
                    chunk_index=chunk.chunk_index,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                )
            )
        return out


def _extract_title(text: str) -> str:
    heading = _HEADING.search(text)
    if heading:
        return heading.group(2).strip()
    # Fallback: first non-blank line.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


def _extract_summary(text: str) -> str:
    # First sentence up to ~240 chars.
    match = re.search(r"[^.!?\n]+[.!?]", text)
    snippet = match.group(0).strip() if match else text.strip()
    return snippet[:240]


def _extract_tags(text: str, max_tags: int = 5) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for word in _WORD.findall(text):
        token = word.lower()
        if token in _STOP or token in seen_set:
            continue
        seen.append(token)
        seen_set.add(token)
        if len(seen) >= max_tags:
            break
    return seen