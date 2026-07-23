"""Reranker implementations."""

from __future__ import annotations

from src.core.types import RetrievalCandidate


class NoneReranker:
    """No-op reranker that preserves the fusion order."""

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        results = list(candidates[:top_k])
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("rerank", {"method": "none", "count": len(results)})
        return results
