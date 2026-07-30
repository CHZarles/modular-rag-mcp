"""混合检索引擎编排。"""

from __future__ import annotations

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
        processed = self.query_processor.process(request, trace=trace)
        filters = _merge_filters(processed.filters, request.filters, request.collection)

        routes: list[tuple[str, Callable[[], list[RetrievalCandidate]]]] = []
        errors: JsonDict = {}

        # 两路互不依赖：并发执行可让远程 Embedding 等待与本地 BM25 查询重叠。
        dense_retriever = self.dense_retriever
        if self.config.enable_dense and dense_retriever is not None:
            routes.append(
                (
                    "dense",
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
            futures = [(name, executor.submit(retrieve)) for name, retrieve in routes]
            # 按 Dense、Sparse 的固定路由顺序取回结果，避免完成时序改变 RRF 同分结果。
            for name, future in futures:
                try:
                    ranked_lists.append(future.result())
                except Exception as exc:
                    # 单路失败时保留另一条通道的结果，满足混合检索的降级语义。
                    errors[name] = str(exc)

        # 所有通道都失败不能伪装成“查询没有命中”，否则上层无法区分空结果与系统故障。
        if not ranked_lists and errors:
            details = "; ".join(f"{name}: {errors[name]}" for name, _ in routes)
            raise RuntimeError(f"all retrieval routes failed: {details}")

        # 融合阶段多保留一批候选，为后续元数据过滤与重排留出余量。
        fused = self.fusion.fuse(
            [ranked for ranked in ranked_lists if ranked],
            top_k=max(request.top_k, self.config.fusion_top_k),
            trace=trace,
        )
        filtered = self.metadata_filter.apply(fused, filters, trace=trace)

        # 重排器属于可选增强，异常时回退到已经过滤的融合顺序。
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

        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage(
                "query_engine",
                {"result_count": len(reranked), "errors": errors},
            )
        return reranked[: request.top_k]


HybridSearch = HybridQueryEngine


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
