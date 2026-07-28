"""重排序器实现。"""

from __future__ import annotations

from src.core.types import RetrievalCandidate


class NoneReranker:
    """保持融合结果原顺序的空操作重排序器。"""

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
