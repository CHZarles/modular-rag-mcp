"""稠密检索适配器：查询向量化后从向量库生成候选结果。"""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate, SearchHit
from src.ports.ingestion import BaseEmbedding, BaseVectorStore


class DenseRetriever:
    """组合 Embedding 与向量存储完成语义召回。"""

    def __init__(self, embedding: BaseEmbedding, vector_store: BaseVectorStore) -> None:
        self.embedding = embedding
        self.vector_store = vector_store

    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        if not query.strip():
            return []
        # 查询只生成一个向量；批量接口由摄取和其他调用方共同复用。
        vectors = self.embedding.embed([query], trace=trace)
        if not vectors:
            return []
        hits = self.vector_store.query(vectors[0], top_k=top_k, filters=filters, trace=trace)
        candidates = [_candidate_from_hit(hit, "dense", rank) for rank, hit in enumerate(hits, start=1)]
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("dense_retrieval", {"count": len(candidates)})
        return candidates


def _candidate_from_hit(hit: SearchHit, source: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=hit.id,
        text=hit.text,
        metadata=dict(hit.metadata),
        score=hit.score,
        source=source,  # type: ignore[arg-type]
        rank=rank,
        debug={"score_kind": hit.score_kind, "raw": hit.raw},
    )
