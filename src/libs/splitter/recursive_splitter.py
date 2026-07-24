"""Recursive character text splitter — stdlib-only LangChain-equivalent.

Splits text by trying a hierarchy of separators (``\n\n``, ``\n``,
``. ``, `` ``, ``""``) so the largest possible semantic boundary is
chosen first. The result is a list of chunks each at most ``chunk_size``
characters long, with ``chunk_overlap`` characters of overlap between
adjacent chunks.

This is intentionally a minimal stdlib re-implementation rather than a
wrapper around ``langchain-text-splitters``: it keeps the dependency
footprint small and makes the splitter behaviour fully testable without
spinning up LangChain.
"""

from __future__ import annotations

import re
from typing import Any

from src.ports.ingestion import BaseSplitter

_DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 80


class RecursiveCharacterSplitter(BaseSplitter):
    """Split text recursively by separator hierarchy, respecting chunk_size."""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        separators: list[str] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError(
                "chunk_overlap must be in [0, chunk_size)"
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or _DEFAULT_SEPARATORS

    def split_text(self, text: str, trace: Any | None = None) -> list[str]:
        if not isinstance(text, str):
            raise ValueError("recursive: text must be a string")
        if not text:
            return []
        # Strip but keep semantic structure intact for headings / code blocks.
        return self._split(text, self.separators)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split(self, text: str, separators: list[str]) -> list[str]:
        final_chunks: list[str] = []
        if len(text) <= self.chunk_size:
            return [text]

        # Find the first separator that actually appears in `text`.
        separator = separators[-1]
        new_separators: list[str] = []
        for i, sep in enumerate(separators):
            if sep == "":
                separator = sep
                break
            if re.search(re.escape(sep), text):
                separator = sep
                new_separators = separators[i + 1 :]
                break

        splits = self._split_by(text, separator)

        # Re-merge small adjacent pieces so the resulting chunks approximate
        # chunk_size without splitting on every tiny separator.
        good_splits: list[str] = []
        for s in splits:
            if len(s) < self.chunk_size:
                good_splits.append(s)
            else:
                if good_splits:
                    merged = self._merge_splits(good_splits, separator)
                    final_chunks.extend(merged)
                    good_splits = []
                if new_separators:
                    final_chunks.extend(self._split(s, new_separators))
                else:
                    final_chunks.append(s)
        if good_splits:
            final_chunks.extend(self._merge_splits(good_splits, separator))

        return [c for c in (chunk.strip() for chunk in final_chunks) if c]

    def _split_by(self, text: str, separator: str) -> list[str]:
        if separator == "":
            # Last resort: chunk by characters.
            return list(text)
        # Keep the separator attached to the preceding piece so we can
        # re-join without losing the boundary marker.
        parts = text.split(separator)
        return [p + separator for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

    def _merge_splits(self, splits: list[str], separator: str) -> list[str]:
        chunks: list[str] = []
        current = ""
        for s in splits:
            candidate = current + s
            if len(candidate) > self.chunk_size and current:
                chunks.append(current)
                # Keep overlap tail for the next chunk.
                if self.chunk_overlap:
                    current = current[-self.chunk_overlap :] + s
                else:
                    current = s
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks