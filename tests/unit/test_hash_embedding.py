from __future__ import annotations

import math
from typing import Any, cast

import pytest

from libs.embedding import BaseEmbedding, EmbeddingFactory, HashEmbedding


def test_factory_creates_deterministic_normalized_hash_vectors() -> None:
    embedding = EmbeddingFactory.create({"embedding": {"provider": "hash", "dimension": 64}})

    first, second = embedding.embed(["Dense retrieval", "Dense retrieval"])

    assert isinstance(embedding, HashEmbedding)
    assert isinstance(embedding, BaseEmbedding)
    assert first == second
    assert len(first) == 64
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)


def test_hash_embedding_preserves_lexical_overlap_for_english_and_chinese() -> None:
    embedding = HashEmbedding({"dimension": 384})
    query, related, unrelated = embedding.embed(
        ["测试查询", "这个测试查询用于离线验收", "reciprocal rank fusion"]
    )

    assert _dot(query, related) > _dot(query, unrelated)


@pytest.mark.parametrize("dimension", [0, -1, True, "384"])
def test_hash_embedding_rejects_invalid_dimension(dimension: Any) -> None:
    with pytest.raises(ValueError, match="dimension must be a positive integer"):
        HashEmbedding({"dimension": dimension})


def test_hash_embedding_rejects_empty_or_invalid_input() -> None:
    embedding = HashEmbedding({})

    with pytest.raises(ValueError, match="non-empty list"):
        embedding.embed([])
    with pytest.raises(ValueError, match=r"texts\[0\]"):
        embedding.embed(cast(Any, [123]))
    with pytest.raises(ValueError, match=r"texts\[0\]"):
        embedding.embed(["  "])

    assert len(embedding.embed(["---"])[0]) == 384


def _dot(left: list[float], right: list[float]) -> float:
    return sum(first * second for first, second in zip(left, right, strict=True))
