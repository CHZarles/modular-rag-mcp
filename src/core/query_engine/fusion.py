"""倒数排名融合（Reciprocal Rank Fusion，RRF）。"""

from __future__ import annotations

from dataclasses import replace
from typing import TypedDict

from src.core.types import RetrievalCandidate


class _RRFContribution(TypedDict):
    """单条召回通道对一个 Chunk 的 RRF 贡献明细。"""

    list_index: int
    source: str
    rank: int
    score: float


class RRFFusion:
    """按候选排名而非原始分数量纲融合多路召回结果。"""

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
        """按各通道中的位置累加 RRF 分数，并返回确定性融合排名。"""
        if top_k <= 0:
            raise ValueError("rrf fusion top_k must be positive")

        scores: dict[str, float] = {}
        first_seen: dict[str, RetrievalCandidate] = {}
        first_positions: dict[str, tuple[int, int]] = {}
        contributions: dict[str, list[_RRFContribution]] = {}

        # 各检索后端的原始分数不可直接比较，因此只累加排名贡献。
        for list_index, ranked in enumerate(ranked_lists):
            seen_in_list: set[str] = set()
            for rank, candidate in enumerate(ranked, start=1):
                chunk_id = candidate.chunk_id
                # 同一通道理论上不应重复返回同一 Chunk；防御性去重避免单路重复加权。
                if chunk_id in seen_in_list:
                    continue
                seen_in_list.add(chunk_id)

                scores[chunk_id] = scores.get(chunk_id, 0.0) + rrf_score(rank, self.k)
                first_seen.setdefault(chunk_id, candidate)
                first_positions.setdefault(chunk_id, (list_index, rank))
                contributions.setdefault(chunk_id, []).append(
                    {
                        "list_index": list_index,
                        "source": candidate.source,
                        "rank": rank,
                        "score": candidate.score,
                    }
                )

        # 分数相同时按首次出现的通道、通道内位置和 Chunk ID 排序，不能信任调用方
        # candidate.rank 中可能残留的旧排名。
        ordered_ids = sorted(
            scores,
            key=lambda chunk_id: (-scores[chunk_id], first_positions[chunk_id], chunk_id),
        )
        fused = [
            replace(
                first_seen[chunk_id],
                score=scores[chunk_id],
                source="fusion",
                rank=index,
                debug={
                    **first_seen[chunk_id].debug,
                    "rrf": {
                        "k": self.k,
                        "score": scores[chunk_id],
                        "sources": contributions[chunk_id],
                    },
                },
            )
            for index, chunk_id in enumerate(ordered_ids[:top_k], start=1)
        ]
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("fusion", {"count": len(fused), "method": "rrf", "k": self.k})
        return fused


def rrf_score(rank: int, k: int = 60) -> float:
    """返回标准 RRF 单路贡献 ``1 / (k + rank)``。"""
    if rank <= 0:
        raise ValueError("rank must be positive")
    if k <= 0:
        raise ValueError("k must be positive")
    return 1.0 / (k + rank)
