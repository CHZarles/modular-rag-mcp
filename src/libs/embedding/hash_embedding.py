"""Deterministic local feature-hashing embeddings for offline engineering workflows."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


class HashEmbedding:
    """Map lexical features into fixed-width vectors without a model or network call."""

    provider = "hash"

    def __init__(self, config: Mapping[str, Any]) -> None:
        dimension = config.get("dimension", 384)
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise ValueError("hash configuration error: dimension must be a positive integer")
        self.dimension = dimension

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        """Encode each non-empty input independently while preserving input order."""
        if not isinstance(texts, list) or not texts:
            raise ValueError("hash input error: texts must be a non-empty list")

        vectors: list[list[float]] = []
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"hash input error: texts[{index}] must be non-empty str")
            vectors.append(self._embed_one(text))
        return vectors

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for feature in _lexical_features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("hash input error: text produced no lexical features")
        return [value / norm for value in vector]


def _lexical_features(text: str) -> Iterable[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = _TOKEN_PATTERN.findall(normalized)
    if not tokens:
        yield f"text:{normalized}"
        return
    for token in tokens:
        yield f"token:{token}"
        if _contains_cjk(token):
            for size in (1, 2, 3):
                for offset in range(len(token) - size + 1):
                    yield f"cjk{size}:{token[offset : offset + size]}"


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


__all__ = ["HashEmbedding"]
