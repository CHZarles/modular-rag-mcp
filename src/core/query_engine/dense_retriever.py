"""稠密检索适配器：查询向量化后从向量库生成候选结果。"""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate, SearchHit
from src.ports.ingestion import BaseEmbedding, BaseVectorStore, GenerationStateStore


class DenseRetriever:
    """组合 Embedding 与向量存储完成语义召回。

    ``generation_store=None`` 只保留给旧索引和独立单元测试；正式分代装配必须注入与
    Pipeline 相同的控制面，否则检索器无法判断命中是否仍是 active generation。
    """

    def __init__(
        self,
        embedding: BaseEmbedding,
        vector_store: BaseVectorStore,
        *,
        generation_store: GenerationStateStore | None = None,
        overfetch_factor: int = 5,
    ) -> None:
        if overfetch_factor <= 0:
            raise ValueError("dense retriever overfetch_factor must be positive")
        self.embedding = embedding
        self.vector_store = vector_store
        self.generation_store = generation_store
        self.overfetch_factor = overfetch_factor

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
        requested_top_k = top_k * self.overfetch_factor if self.generation_store else top_k
        hits = self.vector_store.query(
            vectors[0], top_k=requested_top_k, filters=filters, trace=trace
        )
        if self.generation_store is not None:
            collection = filters.get("collection") if filters else None
            active = self.generation_store.get_active_generations(
                collection if isinstance(collection, str) else None
            )
            hits = [hit for hit in hits if _is_active(hit, active)][:top_k]
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


def _is_active(hit: SearchHit, active: dict[str, int]) -> bool:
    doc_key = hit.metadata.get("doc_key")
    generation = hit.metadata.get("generation")
    return (
        isinstance(doc_key, str)
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and active.get(doc_key) == generation
    )
