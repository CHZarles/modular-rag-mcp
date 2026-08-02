"""混合检索引擎编排。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from src.core.types import JsonDict, QueryRequest, RetrievalCandidate
from src.ports.query import (
    BaseReranker,
    DenseRetriever,
    FusionStrategy,
    MetadataFilter,
    QueryProcessor,
    SparseRetriever,
)


@dataclass(frozen=True)
class HybridSearchConfig:
    """控制各召回通道开关及每阶段候选数量。"""

    dense_top_k: int = 20
    sparse_top_k: int = 20
    fusion_top_k: int = 10
    enable_dense: bool = True
    enable_sparse: bool = True

    def __post_init__(self) -> None:
        """在启动阶段拒绝无效候选数量和没有召回通道的配置。"""
        limits = {
            "dense_top_k": self.dense_top_k,
            "sparse_top_k": self.sparse_top_k,
            "fusion_top_k": self.fusion_top_k,
        }
        for name, value in limits.items():
            if value <= 0:
                raise ValueError(f"hybrid search {name} must be positive")
        if not self.enable_dense and not self.enable_sparse:
            raise ValueError("hybrid search must enable at least one retrieval route")


@dataclass(frozen=True)
class _RouteOutcome:
    candidates: list[RetrievalCandidate]
    elapsed_ms: float
    error: str | None = None


class HybridQueryEngine:
    """执行查询预处理、并行双路召回、融合、过滤与重排序。"""

    def __init__(
        self,
        query_processor: QueryProcessor,
        fusion: FusionStrategy,
        metadata_filter: MetadataFilter,
        reranker: BaseReranker,
        dense_retriever: DenseRetriever | None = None,
        sparse_retriever: SparseRetriever | None = None,
        config: HybridSearchConfig | None = None,
    ) -> None:
        selected_config = config or HybridSearchConfig()
        if selected_config.enable_dense and dense_retriever is None:
            raise ValueError("dense retriever is required when dense route is enabled")
        if selected_config.enable_sparse and sparse_retriever is None:
            raise ValueError("sparse retriever is required when sparse route is enabled")

        self.query_processor = query_processor
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.fusion = fusion
        self.metadata_filter = metadata_filter
        self.reranker = reranker
        self.config = selected_config

    def search(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        search_started = time.monotonic()
        processing_started = time.monotonic()
        try:
            processed = self.query_processor.process(request, trace=trace)
        except Exception as exc:
            _record_trace_stage(
                trace,
                "query_processing",
                method="process",
                provider=type(self.query_processor).__name__,
                details={"status": "error", "error": str(exc)},
                started=processing_started,
            )
            raise
        _record_trace_stage(
            trace,
            "query_processing",
            method="process",
            provider=type(self.query_processor).__name__,
            details={
                "status": "success",
                "keyword_count": len(processed.keywords),
                "filter_count": len(processed.filters),
            },
            started=processing_started,
        )
        filters = _merge_filters(processed.filters, request.filters, request.collection)

        routes: list[tuple[str, str, Callable[[], list[RetrievalCandidate]]]] = []
        errors: JsonDict = {}

        # 两路互不依赖：并发执行可让远程 Embedding 等待与本地 BM25 查询重叠。
        dense_retriever = self.dense_retriever
        if self.config.enable_dense and dense_retriever is not None:
            routes.append(
                (
                    "dense",
                    type(dense_retriever).__name__,
                    lambda: dense_retriever.retrieve(
                        processed.standalone_query,
                        top_k=self.config.dense_top_k,
                        filters=filters,
                        trace=trace,
                    ),
                )
            )

        sparse_retriever = self.sparse_retriever
        if self.config.enable_sparse and sparse_retriever is not None:
            routes.append(
                (
                    "sparse",
                    type(sparse_retriever).__name__,
                    lambda: sparse_retriever.retrieve(
                        processed.keywords,
                        top_k=self.config.sparse_top_k,
                        filters=filters,
                        trace=trace,
                    ),
                )
            )

        ranked_lists: list[list[RetrievalCandidate]] = []
        with ThreadPoolExecutor(
            max_workers=len(routes),
            thread_name_prefix="hybrid-search",
        ) as executor:
            futures = [
                (name, provider, executor.submit(_run_route, retrieve))
                for name, provider, retrieve in routes
            ]
            # 按 Dense、Sparse 的固定路由顺序取回结果，避免完成时序改变 RRF 同分结果。
            for name, provider, future in futures:
                outcome = future.result()
                details: JsonDict = {
                    "status": "error" if outcome.error is not None else "success",
                    "requested_top_k": (
                        self.config.dense_top_k if name == "dense" else self.config.sparse_top_k
                    ),
                    "result_count": len(outcome.candidates),
                    "candidates": _candidate_snapshots(outcome.candidates),
                }
                if outcome.error is not None:
                    details["error"] = outcome.error
                    # 单路失败时保留另一条通道的结果，满足混合检索的降级语义。
                    errors[name] = outcome.error
                else:
                    ranked_lists.append(outcome.candidates)
                _record_trace_stage(
                    trace,
                    f"{name}_retrieval",
                    method=name,
                    provider=provider,
                    details=details,
                    elapsed_ms=outcome.elapsed_ms,
                )

        # 所有通道都失败不能伪装成“查询没有命中”，否则上层无法区分空结果与系统故障。
        if not ranked_lists and errors:
            failure_details = "; ".join(f"{name}: {errors[name]}" for name, _, _ in routes)
            raise RuntimeError(f"all retrieval routes failed: {failure_details}")

        # 融合阶段多保留一批候选，为后续元数据过滤与重排留出余量。
        nonempty_rankings = [ranked for ranked in ranked_lists if ranked]
        fusion_top_k = max(request.top_k, self.config.fusion_top_k)
        fusion_started = time.monotonic()
        try:
            fused = self.fusion.fuse(
                nonempty_rankings,
                top_k=fusion_top_k,
                trace=trace,
            )
        except Exception as exc:
            _record_trace_stage(
                trace,
                "fusion",
                method="fuse",
                provider=type(self.fusion).__name__,
                details={"status": "error", "error": str(exc)},
                started=fusion_started,
            )
            raise
        _record_trace_stage(
            trace,
            "fusion",
            method="fuse",
            provider=type(self.fusion).__name__,
            details={
                "status": "success",
                "input_list_count": len(nonempty_rankings),
                "input_count": sum(len(ranked) for ranked in nonempty_rankings),
                "output_count": len(fused),
                "top_k": fusion_top_k,
                "candidates": _candidate_snapshots(fused),
            },
            started=fusion_started,
        )

        filter_started = time.monotonic()
        filtered = self.metadata_filter.apply(fused, filters, trace=trace)
        _record_trace_stage(
            trace,
            "metadata_filter",
            method="filter",
            provider=type(self.metadata_filter).__name__,
            details={
                "status": "success",
                "input_count": len(fused),
                "output_count": len(filtered),
                "filter_count": len(filters),
                "candidates": _candidate_snapshots(filtered),
            },
            started=filter_started,
        )

        # 重排器属于可选增强，异常时回退到已经过滤的融合顺序。
        rerank_started = time.monotonic()
        try:
            reranked = self.reranker.rerank(
                processed.standalone_query,
                filtered,
                top_k=request.top_k,
                trace=trace,
            )
        except Exception as exc:
            errors["rerank"] = str(exc)
            reranked = filtered[: request.top_k]
            _record_trace_stage(
                trace,
                "rerank",
                method="fallback",
                provider=type(self.reranker).__name__,
                details={
                    "status": "fallback",
                    "error": str(exc),
                    "input_count": len(filtered),
                    "output_count": len(reranked),
                },
                started=rerank_started,
            )

        _record_trace_stage(
            trace,
            "query_engine",
            method="hybrid_search",
            provider=type(self).__name__,
            details={"result_count": len(reranked), "errors": errors},
            started=search_started,
        )
        return reranked[: request.top_k]


HybridSearch = HybridQueryEngine


def _run_route(
    retrieve: Callable[[], list[RetrievalCandidate]],
) -> _RouteOutcome:
    started = time.monotonic()
    try:
        candidates = retrieve()
    except Exception as exc:
        return _RouteOutcome([], _elapsed_ms(started), str(exc) or type(exc).__name__)
    return _RouteOutcome(candidates, _elapsed_ms(started))


def _candidate_snapshots(candidates: list[RetrievalCandidate]) -> list[JsonDict]:
    return [
        {
            "chunk_id": candidate.chunk_id,
            "rank": index,
            "score": candidate.score,
            "source": candidate.source,
            "source_path": candidate.metadata.get("source_path"),
            "title": candidate.metadata.get("title"),
        }
        for index, candidate in enumerate(candidates[:50], start=1)
    ]


def _record_trace_stage(
    trace: object | None,
    stage_name: str,
    *,
    method: str,
    provider: str,
    details: JsonDict,
    started: float | None = None,
    elapsed_ms: float | None = None,
) -> None:
    if trace is None or not hasattr(trace, "record_stage"):
        return
    timing_start = started if started is not None else time.monotonic()
    duration = elapsed_ms if elapsed_ms is not None else _elapsed_ms(timing_start)
    trace.record_stage(
        stage_name,
        {"method": method, "provider": provider, "details": details},
        elapsed_ms=duration,
    )


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _merge_filters(
    processed_filters: JsonDict,
    request_filters: JsonDict,
    collection: str,
) -> JsonDict:
    merged = dict(processed_filters)
    merged.update(request_filters)
    if collection:
        # collection 是 QueryRequest 的专用作用域字段，应覆盖通用 filters 中的冲突值。
        merged["collection"] = collection
    return merged
