"""Tests for B7.8: Cross-Encoder Reranker (mock scorer)."""

from __future__ import annotations

import pytest

from src.core.settings import RerankConfig
from src.core.types import RetrievalCandidate
from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
from src.libs.reranker.llm_reranker import RerankerFallback


def _cands(*pairs: tuple[str, str]) -> list[RetrievalCandidate]:
    """Build candidates from (chunk_id, text) pairs."""
    return [
        RetrievalCandidate(
            chunk_id=cid, text=txt, metadata={}, score=1.0, source="fusion", rank=i
        )
        for i, (cid, txt) in enumerate(pairs)
    ]


def _mock_scorer(score_map: dict[str, float]):
    def scorer(query: str, text: str) -> float:
        for cid, s in score_map.items():
            if cid in text:
                return s
        return 0.0
    return scorer


def test_cross_encoder_sorts_descending_by_score() -> None:
    reranker = CrossEncoderReranker(
        RerankConfig(backend="cross_encoder"),
        scorer=_mock_scorer({"a": 0.9, "b": 0.5, "c": 0.7}),
    )
    out = reranker.rerank("query", _cands(("a", "doc a"), ("b", "doc b"), ("c", "doc c")), top_k=3)
    assert [c.chunk_id for c in out] == ["a", "c", "b"]


def test_cross_encoder_respects_top_k() -> None:
    reranker = CrossEncoderReranker(
        RerankConfig(backend="cross_encoder"),
        scorer=_mock_scorer({"a": 0.9, "b": 0.8, "c": 0.7}),
    )
    out = reranker.rerank("q", _cands(("a", "a"), ("b", "b"), ("c", "c")), top_k=2)
    assert [c.chunk_id for c in out] == ["a", "b"]


def test_cross_encoder_top_m_caps_input_pool() -> None:
    scored: list[tuple[str, float]] = []

    def scorer(query, text):
        scored.append((text, 0.0))
        return 0.0

    reranker = CrossEncoderReranker(
        RerankConfig(backend="cross_encoder", top_m=2),
        scorer=scorer,
    )
    reranker.rerank(
        "q", _cands(("a", "a"), ("b", "b"), ("c", "c"), ("d", "d")), top_k=4
    )
    assert len(scored) == 2  # only first 2 candidates scored


def test_cross_encoder_empty_candidates_returns_empty() -> None:
    reranker = CrossEncoderReranker(RerankConfig(backend="cross_encoder"), scorer=lambda q, t: 0.0)
    assert reranker.rerank("q", [], top_k=5) == []


def test_cross_encoder_invalid_top_k_raises() -> None:
    reranker = CrossEncoderReranker(RerankConfig(backend="cross_encoder"), scorer=lambda q, t: 0.0)
    with pytest.raises(ValueError, match="top_k"):
        reranker.rerank("q", _cands(("a", "a")), top_k=0)


def test_cross_encoder_blank_query_raises() -> None:
    reranker = CrossEncoderReranker(RerankConfig(backend="cross_encoder"), scorer=lambda q, t: 0.0)
    with pytest.raises(ValueError, match="non-empty"):
        reranker.rerank("", _cands(("a", "a")), top_k=1)


def test_cross_encoder_scorer_failure_raises_reranker_fallback() -> None:
    def scorer(query, text):
        raise TimeoutError("model timed out")

    reranker = CrossEncoderReranker(RerankConfig(backend="cross_encoder"), scorer=scorer)
    with pytest.raises(RerankerFallback, match="scorer failed"):
        reranker.rerank("q", _cands(("a", "a")), top_k=1)


def test_factory_routes_cross_encoder() -> None:
    from src.libs.reranker.reranker_factory import RerankerFactory

    snapshot = dict(RerankerFactory._registry)
    RerankerFactory.reset()
    try:
        RerankerFactory.register(
            "cross_encoder",
            lambda c: CrossEncoderReranker(c, scorer=lambda q, t: 0.0),
        )
        reranker = RerankerFactory.create(RerankConfig(backend="cross_encoder"))
        assert isinstance(reranker, CrossEncoderReranker)
    finally:
        RerankerFactory._registry.clear()
        RerankerFactory._registry.update(snapshot)