"""Sparse encoder — tokenises chunk text into a BM25-friendly term dict."""

from __future__ import annotations

import re
from typing import Any

from src.core.types import Chunk, JsonDict

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your",
    "are", "but", "not", "have", "has", "had", "was", "were", "they",
    "their", "them", "its", "our", "out", "any", "all",
}


class SparseEncoder:
    """Pure-Python tokenizer → term-frequency dict (no external deps)."""

    def encode(self, chunks: list[Chunk], trace: Any | None = None) -> list[JsonDict]:
        return [_tokenize(c.text) for c in chunks]


def _tokenize(text: str) -> JsonDict:
    terms: dict[str, int] = {}
    for token in _WORD.findall(text.lower()):
        if token in _STOP:
            continue
        terms[token] = terms.get(token, 0) + 1
    return {"terms": terms}