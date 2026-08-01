"""HybridSearch 对查询预处理、双路召回、RRF 与过滤的集成测试。"""

from __future__ import annotations

from threading import Barrier
from typing import Literal

import pytest

from src.core.query_engine import (
    ExactMetadataFilter,
    HybridQueryEngine,
    HybridSearchConfig,
    NoneReranker,
    QueryProcessor,
    RRFFusion,
)
from src.core.trace import TraceContext
from src.core.types import JsonDict, QueryRequest, RetrievalCandidate
from src.ports.query import QueryEngine


class FakeDenseRetriever:
    def __init__(
        self,
        candidates: list[RetrievalCandidate],
        *,
        barrier: Barrier | None = None,
        error: Exception | None = None,
    ) -> None:
        self.candidates = candidates
        self.barrier = barrier
        self.error = error
        self.calls: list[tuple[str, int, JsonDict | None]] = []
        self.traces: list[object | None] = []

    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append((query, top_k, filters))
        self.traces.append(trace)
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        if self.error is not None:
            raise self.error
        return self.candidates


class FakeSparseRetriever:
    def __init__(
        self,
        candidates: list[RetrievalCandidate],
        *,
        barrier: Barrier | None = None,
        error: Exception | None = None,
    ) -> None:
        self.candidates = candidates
        self.barrier = barrier
        self.error = error
        self.calls: list[tuple[list[str], int, JsonDict | None]] = []
        self.traces: list[object | None] = []

    def retrieve(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append((keywords, top_k, filters))
        self.traces.append(trace)
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        if self.error is not None:
            raise self.error
        return self.candidates


def test_search_runs_dense_and_sparse_in_parallel_then_fuses_results() -> None:
    barrier = Barrier(2)
    dense = FakeDenseRetriever(
        [
            _candidate("a", "dense", rank=1, metadata={"kind": "guide"}),
            _candidate("b", "dense", rank=2, metadata={"kind": "guide"}),
        ],
        barrier=barrier,
    )
    sparse = FakeSparseRetriever(
        [
            _candidate("b", "sparse", rank=1, metadata={"kind": "guide"}),
            _candidate("c", "sparse", rank=2, metadata={"kind": "note"}),
        ],
        barrier=barrier,
    )
    engine = _engine(
        dense=dense,
        sparse=sparse,
        config=HybridSearchConfig(dense_top_k=7, sparse_top_k=8, fusion_top_k=3),
    )

    results = engine.search(
        QueryRequest(
            query="  lease   generation  ",
            collection="docs",
            # collection 是独立的一等字段，不能被通用 filters 中的冲突值改写。
            filters={"kind": "guide", "collection": "other"},
            top_k=2,
        )
    )

    filters = {"kind": "guide", "collection": "docs"}
    assert dense.calls == [("lease generation", 7, filters)]
    assert sparse.calls == [(["lease", "generation"], 8, filters)]
    assert [result.chunk_id for result in results] == ["b", "a"]
    assert [result.rank for result in results] == [1, 2]
    assert all(result.source == "fusion" for result in results)


def test_search_records_timed_query_stages_with_methods_and_details() -> None:
    dense = FakeDenseRetriever([_candidate("d", "dense", rank=1)])
    sparse = FakeSparseRetriever([_candidate("s", "sparse", rank=1)])
    engine = _engine(dense=dense, sparse=sparse)
    trace = TraceContext(trace_type="query")

    results = engine.search(
        QueryRequest(query="lease generation", collection="docs", top_k=2),
        trace=trace,
    )
    trace.finish()

    required_stages = {
        "query_processing",
        "dense_retrieval",
        "sparse_retrieval",
        "fusion",
        "rerank",
    }
    stages = {stage["stage"]: stage for stage in trace.stages}
    assert required_stages <= stages.keys()
    for stage_name in required_stages:
        stage = stages[stage_name]
        assert isinstance(stage["elapsed_ms"], float)
        assert stage["elapsed_ms"] >= 0
        assert isinstance(stage["data"]["method"], str)
        assert stage["data"]["method"]
        assert isinstance(stage["data"]["provider"], str)
        assert isinstance(stage["data"]["details"], dict)

    assert trace.to_dict()["trace_type"] == "query"
    assert dense.traces == [trace]
    assert sparse.traces == [trace]
    assert [result.chunk_id for result in results] == ["d", "s"]


def test_search_keeps_results_when_one_retrieval_route_fails() -> None:
    dense = FakeDenseRetriever([], error=RuntimeError("embedding unavailable"))
    sparse = FakeSparseRetriever([_candidate("s", "sparse", rank=1)])
    engine = _engine(dense=dense, sparse=sparse)
    trace = TraceContext()

    results = engine.search(
        QueryRequest(query="lease", collection="docs", top_k=1),
        trace=trace,
    )

    assert [result.chunk_id for result in results] == ["s"]
    dense_stage = next(stage for stage in trace.stages if stage["stage"] == "dense_retrieval")
    sparse_stage = next(stage for stage in trace.stages if stage["stage"] == "sparse_retrieval")
    assert dense_stage["data"]["details"]["status"] == "error"
    assert dense_stage["data"]["details"]["error"] == "embedding unavailable"
    assert sparse_stage["data"]["details"]["status"] == "success"


def test_search_raises_when_every_enabled_route_fails() -> None:
    engine = _engine(
        dense=FakeDenseRetriever([], error=RuntimeError("dense down")),
        sparse=FakeSparseRetriever([], error=RuntimeError("sparse down")),
    )

    with pytest.raises(RuntimeError, match="dense.*dense down.*sparse.*sparse down"):
        engine.search(QueryRequest(query="lease", collection="docs"))


def test_search_supports_explicit_dense_only_mode() -> None:
    dense = FakeDenseRetriever([_candidate("d", "dense", rank=1)])
    engine = _engine(
        dense=dense,
        sparse=None,
        config=HybridSearchConfig(enable_sparse=False),
    )

    results = engine.search(QueryRequest(query="semantic query", collection="docs"))

    assert [result.chunk_id for result in results] == ["d"]
    assert isinstance(engine, QueryEngine)


@pytest.mark.parametrize(
    "overrides",
    [
        {"dense_top_k": 0},
        {"sparse_top_k": 0},
        {"fusion_top_k": 0},
        {"enable_dense": False, "enable_sparse": False},
    ],
)
def test_config_rejects_invalid_route_and_candidate_limits(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        HybridSearchConfig(**overrides)  # type: ignore[arg-type]


def test_engine_requires_implementation_for_each_enabled_route() -> None:
    with pytest.raises(ValueError, match="dense retriever is required"):
        _engine(dense=None, sparse=FakeSparseRetriever([]))

    with pytest.raises(ValueError, match="sparse retriever is required"):
        _engine(dense=FakeDenseRetriever([]), sparse=None)


def _engine(
    *,
    dense: FakeDenseRetriever | None,
    sparse: FakeSparseRetriever | None,
    config: HybridSearchConfig | None = None,
) -> HybridQueryEngine:
    return HybridQueryEngine(
        query_processor=QueryProcessor(),
        dense_retriever=dense,
        sparse_retriever=sparse,
        fusion=RRFFusion(),
        metadata_filter=ExactMetadataFilter(),
        reranker=NoneReranker(),
        config=config,
    )


def _candidate(
    chunk_id: str,
    source: Literal["dense", "sparse"],
    *,
    rank: int,
    metadata: JsonDict | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text-{chunk_id}",
        metadata={"collection": "docs", **(metadata or {})},
        score=float(rank),
        source=source,
        rank=rank,
    )
