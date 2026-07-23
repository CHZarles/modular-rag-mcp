"""Reciprocal Rank Fusion."""

from __future__ import annotations

from dataclasses import replace

from src.core.types import RetrievalCandidate


class RRFFusion:
    def __init__(self, k: int = 60) -> None:
        if k <= 0:
            raise ValueError("k must be positive")
        self.k = k

    def fuse(
        self,
        ranked_lists: list[list[RetrievalCandidate]],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        scores: dict[str, float] = {}
        first_seen: dict[str, RetrievalCandidate] = {}
        debug: dict[str, dict[str, object]] = {}

        for list_index, ranked in enumerate(ranked_lists):
            for rank, candidate in enumerate(ranked, start=1):
                chunk_id = candidate.chunk_id
                scores[chunk_id] = scores.get(chunk_id, 0.0) + rrf_score(rank, self.k)
                first_seen.setdefault(chunk_id, candidate)
                debug.setdefault(chunk_id, {"sources": []})
                debug[chunk_id]["sources"].append(
                    {
                        "list_index": list_index,
                        "source": candidate.source,
                        "rank": rank,
                        "score": candidate.score,
                    }
                )

        ordered_ids = sorted(
            scores,
            key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id].rank, chunk_id),
        )
        fused = [
            replace(
                first_seen[chunk_id],
                score=scores[chunk_id],
                source="fusion",
                rank=index,
                debug={**first_seen[chunk_id].debug, "rrf": debug[chunk_id]},
            )
            for index, chunk_id in enumerate(ordered_ids[:top_k], start=1)
        ]
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("fusion", {"count": len(fused), "method": "rrf", "k": self.k})
        return fused


def rrf_score(rank: int, k: int = 60) -> float:
    if rank <= 0:
        raise ValueError("rank must be positive")
    if k <= 0:
        raise ValueError("k must be positive")
    return 1.0 / (k + rank)
