"""Tests for D1-D5: query processor + retrievers + fusion + hybrid search."""

from __future__ import annotations

import pytest

from src.core.query_engine.dense_retriever import DenseRetriever
from src.core.query_engine.filter import ExactMetadataFilter
from src.core.query_engine.fusion import RRFFusion
from src.core.query_engine.hybrid_search import HybridQueryEngine, HybridSearchConfig
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.reranker import NoneReranker
from src.core.query_engine.sparse_retriever import SparseRetriever
from src.core.types import Chunk, QueryRequest, SearchHit
from src.ingestion.storage.bm25_indexer import BM25IndexStore
from src.ports.ingestion import BaseEmbedding, BaseVectorStore


def _chunk(cid: str, text: str = "x") -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata={"source_path": "a.pdf", "collection": "docs"},
        source_ref="a",
        chunk_index=0,
    )


# ---------------------------------------------------------------------------
# D1: QueryProcessor
# ---------------------------------------------------------------------------


def test_query_processor_extracts_keywords() -> None:
    pq = QueryProcessor().process(QueryRequest(query="python tutorial"))
    assert "python" in pq.keywords
    assert "tutorial" in pq.keywords


def test_query_processor_dedupes_keywords() -> None:
    pq = QueryProcessor().process(QueryRequest(query="python Python java java"))
    assert pq.keywords.count("python") == 1
    assert pq.keywords.count("java") == 1


def test_query_processor_propagates_filters() -> None:
    pq = QueryProcessor().process(
        QueryRequest(query="x", filters={"collection": "kb"})
    )
    assert pq.filters == {"collection": "kb"}


# ---------------------------------------------------------------------------
# D2: DenseRetriever
# ---------------------------------------------------------------------------


class _FakeEmbedding(BaseEmbedding):
    def __init__(self, dim: int = 3) -> None:
        self.dim = dim

    def embed(self, texts, trace=None):
        return [[float(len(t))] * self.dim for t in texts]


class _FakeVectorStore(BaseVectorStore):
    def __init__(self, hits):
        self.hits = list(hits)
        self.calls = []

    def upsert(self, records, trace=None): pass
    def query(self, vector, top_k, filters=None, trace=None):
        self.calls.append((list(vector), top_k, dict(filters or {})))
        return self.hits[:top_k]
    def get_by_ids(self, ids): return []
    def delete_by_metadata(self, filters): return 0


def test_dense_retriever_returns_candidates() -> None:
    hits = [
        SearchHit(id="a", text="text a", metadata={"x": 1}, score=0.9, score_kind="similarity"),
        SearchHit(id="b", text="text b", metadata={"x": 2}, score=0.5, score_kind="similarity"),
    ]
    store = _FakeVectorStore(hits)
    retriever = DenseRetriever(_FakeEmbedding(), store)
    out = retriever.retrieve("query", top_k=2, filters={"collection": "kb"})
    assert [c.chunk_id for c in out] == ["a", "b"]
    assert all(c.source == "dense" for c in out)
    assert store.calls[0][2] == {"collection": "kb"}


def test_dense_retriever_empty_embedding_returns_empty() -> None:
    class ZeroEmbedding(BaseEmbedding):
        def embed(self, texts, trace=None):
            return []
    store = _FakeVectorStore([])
    retriever = DenseRetriever(ZeroEmbedding(), store)
    assert retriever.retrieve("q", top_k=5) == []


# ---------------------------------------------------------------------------
# D3: SparseRetriever
# ---------------------------------------------------------------------------


def test_sparse_retriever_returns_candidates() -> None:
    bm25 = BM25IndexStore()
    bm25.upsert([_chunk("a", "alpha"), _chunk("b", "beta")])
    out = SparseRetriever(bm25).retrieve(["alpha"], top_k=5)
    assert {c.chunk_id for c in out} == {"a"}
    assert out[0].source == "sparse"


def test_sparse_retriever_empty_keywords_returns_empty() -> None:
    bm25 = BM25IndexStore()
    bm25.upsert([_chunk("a", "alpha")])
    assert SparseRetriever(bm25).retrieve([], top_k=5) == []


# ---------------------------------------------------------------------------
# D4: RRFFusion
# ---------------------------------------------------------------------------


def _candidate(cid: str, score: float, source: str):
    from src.core.types import RetrievalCandidate
    return RetrievalCandidate(
        chunk_id=cid,
        text=f"text {cid}",
        metadata={},
        score=score,
        source=source,  # type: ignore[arg-type]
        rank=0,
    )


def test_rrf_fuses_two_lists() -> None:
    fusion = RRFFusion(k=60)
    dense = [_candidate("a", 0.9, "dense"), _candidate("b", 0.5, "dense"), _candidate("c", 0.1, "dense")]
    sparse = [_candidate("b", 5.0, "sparse"), _candidate("d", 3.0, "sparse")]
    out = fusion.fuse([dense, sparse], top_k=4)
    assert [c.chunk_id for c in out] == ["b", "a", "d", "c"]


def test_rrf_empty_lists() -> None:
    assert RRFFusion(k=60).fuse([], top_k=5) == []


def test_rrf_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError):
        RRFFusion(k=0)


# ---------------------------------------------------------------------------
# D5: HybridQueryEngine
# ---------------------------------------------------------------------------


def test_hybrid_search_fuses_dense_and_sparse() -> None:
    bm25 = BM25IndexStore()
    bm25.upsert([_chunk("b", "beta")])
    embedding = _FakeEmbedding()
    hits = [
        SearchHit(
            id="a",
            text="text a",
            metadata={"collection": "docs"},
            score=0.9,
            score_kind="similarity",
        ),
    ]
    vector_store = _FakeVectorStore(hits)

    dense = DenseRetriever(embedding, vector_store)
    sparse = SparseRetriever(bm25)
    fusion = RRFFusion(k=60)

    engine = HybridQueryEngine(
        query_processor=QueryProcessor(),
        fusion=fusion,
        metadata_filter=ExactMetadataFilter(),
        reranker=NoneReranker(),
        dense_retriever=dense,
        sparse_retriever=sparse,
        config=HybridSearchConfig(dense_top_k=5, sparse_top_k=5, fusion_top_k=5),
    )

    out = engine.search(QueryRequest(query="alpha beta", collection="docs", top_k=5))
    ids = {c.chunk_id for c in out}
    assert ids <= {"a", "b"}
    assert "a" in ids and "b" in ids