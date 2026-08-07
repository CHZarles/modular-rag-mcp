"""稠密检索适配器：查询向量化后从向量库生成候选结果。"""

from __future__ import annotations

from collections.abc import Callable

from src.core.types import JsonDict, RetrievalCandidate, SearchHit
from src.ports.ingestion import (
    BaseEmbedding,
    BaseVectorStore,
    GenerationStateStore,
    QueryEmbedding,
)


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
        dimension_validator: Callable[[int], None] | None = None,
    ) -> None:
        if overfetch_factor <= 0:
            raise ValueError("dense retriever overfetch_factor must be positive")
        self.embedding = embedding
        self.vector_store = vector_store
        self.generation_store = generation_store
        self.overfetch_factor = overfetch_factor
        self.dimension_validator = dimension_validator

    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        """把完整查询编码一次，并将底层命中规范化为有序 Dense 候选。"""
        if top_k <= 0:
            raise ValueError("dense retriever top_k must be positive")
        if not query.strip():
            return []
        # MiniMax 等 Provider 会区分 query/db；其余 Provider 继续复用批量接口。
        if isinstance(self.embedding, QueryEmbedding):
            vectors = [self.embedding.embed_query(query, trace=trace)]
        else:
            vectors = self.embedding.embed([query], trace=trace)
        if len(vectors) != 1:
            raise ValueError("dense retriever embedding count must equal one")
        if not vectors[0]:
            raise ValueError("dense retriever query embedding must not be empty")
        if self.dimension_validator is not None:
            self.dimension_validator(len(vectors[0]))
        requested_top_k = top_k * self.overfetch_factor if self.generation_store else top_k
        hits = self.vector_store.query(
            vectors[0], top_k=requested_top_k, filters=filters, trace=trace
        )
        if self.generation_store is not None:
            collection = filters.get("collection") if filters else None
            active = self.generation_store.get_active_generations(
                collection if isinstance(collection, str) else None
            )
            hits = [hit for hit in hits if _is_active(hit, active)]
        # 即使某个自定义 VectorStore 返回了过多命中，也不能突破调用方声明的 top_k。
        hits = hits[:top_k]
        candidates = [
            _candidate_from_hit(hit, rank) for rank, hit in enumerate(hits, start=1)
        ]
        return candidates


def _candidate_from_hit(hit: SearchHit, rank: int) -> RetrievalCandidate:
    """保留正文、元数据和原始分数语义，并补充 Dense 来源与稳定排名。"""
    return RetrievalCandidate(
        chunk_id=hit.id,
        text=hit.text,
        metadata=dict(hit.metadata),
        score=hit.score,
        source="dense",
        rank=rank,
        debug={"score_kind": hit.score_kind, "raw": dict(hit.raw)},
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
