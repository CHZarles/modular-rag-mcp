"""Cross-Encoder 检索候选精排后端。"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from src.core.types import RetrievalCandidate

PairScorer = Callable[[str, str], float]


class CrossEncoderModel(Protocol):
    """兼容 ``sentence_transformers.CrossEncoder`` 的最小模型接口。"""

    def predict(self, pairs: list[tuple[str, str]]) -> Any: ...


class CrossEncoderReranker:
    """联合读取 Query 与候选正文，按相关性分数降序排列。"""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        scorer: PairScorer | None = None,
        model: CrossEncoderModel | None = None,
    ) -> None:
        self.config = config or {}
        configured_scorer = self.config.get("scorer")
        if scorer is None and callable(configured_scorer):
            scorer = configured_scorer
        self.scorer = scorer
        self.model = model
        self.model_name = str(self.config.get("model", "")).strip()

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        if top_k <= 0:
            raise ValueError("cross-encoder reranker top_k must be positive")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("cross-encoder reranker query must be a non-empty string")
        if not candidates:
            return []

        scores = self._score(query, candidates)
        indexed = list(enumerate(zip(candidates, scores, strict=True)))
        indexed.sort(key=lambda item: (-item[1][1], item[0]))
        return [
            replace(
                candidate,
                score=score,
                source="rerank",
                rank=rank,
                debug={
                    **candidate.debug,
                    "rerank": {
                        "backend": "cross_encoder",
                        "fallback": False,
                        "score": score,
                        "previous_score": candidate.score,
                    },
                },
            )
            for rank, (_, (candidate, score)) in enumerate(indexed[:top_k], start=1)
        ]

    def _score(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
    ) -> list[float]:
        pairs = [(query, candidate.text) for candidate in candidates]
        try:
            if self.scorer is not None:
                raw_scores: Any = [self.scorer(query, candidate.text) for candidate in candidates]
            else:
                model = self._get_model()
                raw_scores = model.predict(pairs)
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            raise RuntimeError(f"cross-encoder scorer failed: {reason}") from exc
        return _validate_scores(raw_scores, expected_count=len(candidates))

    def _get_model(self) -> CrossEncoderModel:
        if self.model is not None:
            return self.model
        if not self.model_name:
            raise ValueError("cross-encoder configuration error: missing model")
        self.model = _load_model(self.model_name)
        return self.model


def _load_model(model_name: str) -> CrossEncoderModel:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "cross-encoder dependency error: install sentence-transformers"
        ) from exc
    return CrossEncoder(model_name)


def _validate_scores(raw_scores: Any, expected_count: int) -> list[float]:
    if hasattr(raw_scores, "tolist"):
        raw_scores = raw_scores.tolist()
    if not isinstance(raw_scores, Sequence) or isinstance(raw_scores, (str, bytes)):
        raise RuntimeError("cross-encoder response error: scores must be a sequence")
    if len(raw_scores) != expected_count:
        raise RuntimeError("cross-encoder response error: score count does not match candidates")

    scores: list[float] = []
    for value in raw_scores:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RuntimeError("cross-encoder response error: scores must be numeric")
        score = float(value)
        if not math.isfinite(score):
            raise RuntimeError("cross-encoder response error: scores must be finite")
        scores.append(score)
    return scores


__all__ = ["CrossEncoderModel", "CrossEncoderReranker", "PairScorer"]
