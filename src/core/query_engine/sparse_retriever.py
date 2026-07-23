"""Sparse retrieval adapter: BM25 index -> candidates."""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate, SearchHit
from src.ports.ingestion import BM25IndexStore


class SparseRetriever:
    def __init__(self, bm25_store: BM25IndexStore) -> None:
        self.bm25_store = bm25_store

    def retrieve(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        if not keywords:
            return []
        hits = self.bm25_store.query(keywords, top_k=top_k, filters=filters, trace=trace)
        candidates = [_candidate_from_hit(hit, rank) for rank, hit in enumerate(hits, start=1)]
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("sparse_retrieval", {"count": len(candidates)})
        return candidates


def _candidate_from_hit(hit: SearchHit, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=hit.id,
        text=hit.text,
        metadata=dict(hit.metadata),
        score=hit.score,
        source="sparse",
        rank=rank,
        debug={"score_kind": hit.score_kind, "raw": hit.raw},
    )
