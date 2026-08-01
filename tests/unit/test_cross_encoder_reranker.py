from __future__ import annotations

from typing import Any

import pytest

from core.types import RetrievalCandidate
from libs.reranker import CrossEncoderReranker, RerankerFactory
from src.core.query_engine.reranker import FallbackReranker


class RecordingModel:
    def __init__(self, scores: Any) -> None:
        self.scores = scores
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> Any:
        self.calls.append(pairs)
        return self.scores


class ArrayLikeScores:
    def tolist(self) -> list[float]:
        return [0.2, 0.8]


def candidate(chunk_id: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"content {chunk_id}",
        metadata={"collection": "docs"},
        score=1.0 / rank,
        source="fusion",
        rank=rank,
        debug={"rrf": {"rank": rank}},
    )


def test_cross_encoder_sorts_by_score_and_updates_rerank_fields() -> None:
    scores = {"a": 0.2, "b": 0.9, "c": 0.5}
    reranker = CrossEncoderReranker(scorer=lambda query, text: scores[text[-1]])
    original = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

    results = reranker.rerank("lease ownership", original, top_k=2)

    assert [item.chunk_id for item in results] == ["b", "c"]
    assert [item.score for item in results] == [0.9, 0.5]
    assert [item.rank for item in results] == [1, 2]
    assert all(item.source == "rerank" for item in results)
    assert results[0].debug == {
        "rrf": {"rank": 2},
        "rerank": {
            "backend": "cross_encoder",
            "fallback": False,
            "score": 0.9,
            "previous_score": 0.5,
        },
    }
    assert original[1].source == "fusion"
    assert "rerank" not in original[1].debug


def test_cross_encoder_keeps_input_order_when_scores_tie() -> None:
    reranker = CrossEncoderReranker(scorer=lambda query, text: 0.5)

    results = reranker.rerank(
        "query",
        [candidate("a", 1), candidate("b", 2), candidate("c", 3)],
        top_k=3,
    )

    assert [item.chunk_id for item in results] == ["a", "b", "c"]


def test_cross_encoder_batches_pairs_through_injected_model() -> None:
    model = RecordingModel(ArrayLikeScores())
    reranker = CrossEncoderReranker(model=model)

    results = reranker.rerank(
        "query",
        [candidate("a", 1), candidate("b", 2)],
        top_k=2,
    )

    assert model.calls == [[("query", "content a"), ("query", "content b")]]
    assert [item.chunk_id for item in results] == ["b", "a"]


def test_factory_creates_cross_encoder_and_outer_layer_limits_top_m() -> None:
    scored_texts: list[str] = []

    def scorer(query: str, text: str) -> float:
        scored_texts.append(text)
        return {"content a": 0.1, "content b": 0.9}[text]

    reranker = RerankerFactory.create(
        {
            "backend": "cross_encoder",
            "model": "unused-in-test",
            "scorer": scorer,
            "top_m": 2,
        }
    )
    results = reranker.rerank(
        "query",
        [candidate("a", 1), candidate("b", 2), candidate("c", 3)],
        top_k=1,
    )

    assert isinstance(reranker, FallbackReranker)
    assert isinstance(reranker.backend, CrossEncoderReranker)
    assert scored_texts == ["content a", "content b"]
    assert [item.chunk_id for item in results] == ["b"]


def test_factory_falls_back_when_cross_encoder_scorer_fails() -> None:
    def failing_scorer(query: str, text: str) -> float:
        raise TimeoutError("hosted scorer timed out")

    reranker = RerankerFactory.create(
        {"backend": "cross_encoder", "scorer": failing_scorer}
    )

    results = reranker.rerank(
        "query",
        [candidate("a", 1), candidate("b", 2)],
        top_k=1,
    )

    assert [item.chunk_id for item in results] == ["a"]
    assert results[0].debug["rerank"] == {
        "backend": "cross_encoder",
        "fallback": True,
        "reason": "cross-encoder scorer failed: hosted scorer timed out",
    }


@pytest.mark.parametrize(
    ("scores", "message"),
    [
        (0.5, "scores must be a sequence"),
        ([0.5], "score count does not match"),
        ([0.5, True], "scores must be numeric"),
        ([0.5, "high"], "scores must be numeric"),
        ([0.5, float("nan")], "scores must be finite"),
    ],
)
def test_cross_encoder_rejects_invalid_model_scores(scores: Any, message: str) -> None:
    reranker = CrossEncoderReranker(model=RecordingModel(scores))

    with pytest.raises(RuntimeError, match=message):
        reranker.rerank("query", [candidate("a", 1), candidate("b", 2)], top_k=1)


def test_cross_encoder_validates_input_without_scoring() -> None:
    calls: list[str] = []

    def scorer(query: str, text: str) -> float:
        calls.append(text)
        return 0.0

    reranker = CrossEncoderReranker(scorer=scorer)

    assert reranker.rerank("query", [], top_k=1) == []
    assert calls == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        reranker.rerank("query", [candidate("a", 1)], top_k=0)
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        reranker.rerank("  ", [candidate("a", 1)], top_k=1)
