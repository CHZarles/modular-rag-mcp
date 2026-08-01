"""稀疏检索适配器：从 BM25 索引生成候选结果。"""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate, SearchHit
from src.ports.ingestion import BM25IndexStore


class SparseRetriever:
    """使用 D1 提取的查询词元在 BM25 索引中完成关键词召回。"""

    def __init__(self, bm25_store: BM25IndexStore) -> None:
        self.bm25_store = bm25_store

    def retrieve(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        """查询 BM25，并把带正文的存储命中转换为有序 Sparse 候选。"""
        if top_k <= 0:
            raise ValueError("sparse retriever top_k must be positive")
        if not keywords:
            return []
        hits = self.bm25_store.query(keywords, top_k=top_k, filters=filters, trace=trace)
        # BM25IndexStore 已经返回正文和 metadata，不需要再依赖 Chroma 做跨存储拼接。
        hits = hits[:top_k]
        candidates = [
            _candidate_from_hit(hit, rank) for rank, hit in enumerate(hits, start=1)
        ]
        return candidates


def _candidate_from_hit(hit: SearchHit, rank: int) -> RetrievalCandidate:
    """保留 BM25 分数与匹配详情，并隔离底层命中的可变字典。"""
    return RetrievalCandidate(
        chunk_id=hit.id,
        text=hit.text,
        metadata=dict(hit.metadata),
        score=hit.score,
        source="sparse",
        rank=rank,
        debug={"score_kind": hit.score_kind, "raw": dict(hit.raw)},
    )
