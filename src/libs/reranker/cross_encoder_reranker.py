"""Cross-Encoder Reranker — scores each (query, candidate) pair with a cross-encoder.

The scorer is injected as a callable so unit tests can supply a deterministic
function. In production this would wrap e.g. ``sentence_transformers``
CrossEncoder model predictions.

Failure semantics: any exception from the scorer (timeout, OOM, etc.) is
re-raised as :class:`RerankerFallback` so :class:`Core.query_engine.reranker`
can fall back to the input order.
"""

from __future__ import annotations

from typing import Any, Callable

from src.core.settings import RerankConfig
from src.core.types import RetrievalCandidate
from src.libs.reranker.llm_reranker import RerankerFallback
from src.ports.query import BaseReranker

Scorer = Callable[[str, str], float]


class CrossEncoderReranker(BaseReranker):
    """Score with a cross-encoder then sort by descending score."""

    def __init__(
        self,
        config: RerankConfig,
        scorer: Scorer,
        *,
        top_m: int | None = None,
    ) -> None:
        self.config = config
        self.scorer = scorer
        self.top_m = top_m if top_m is not None else config.top_m

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        if top_k <= 0:
            raise ValueError("cross-encoder reranker: top_k must be > 0")
        if not isinstance(query, str) or not query:
            raise ValueError("cross-encoder reranker: query must be a non-empty string")

        # Optionally cap at top_m before scoring to bound compute.
        pool = list(candidates)
        if self.top_m:
            pool = pool[: self.top_m]

        scored: list[tuple[float, RetrievalCandidate]] = []
        for c in pool:
            try:
                score = float(self.scorer(query, c.text))
            except Exception as exc:  # noqa: BLE001 — surface as fallback signal
                raise RerankerFallback(
                    f"cross-encoder reranker: scorer failed: {exc}"
                ) from exc
            scored.append((score, c))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _, c in scored[:top_k]]