"""Hybrid query engine orchestration."""

from __future__ import annotations

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
    dense_top_k: int = 20
    sparse_top_k: int = 20
    fusion_top_k: int = 10
    enable_dense: bool = True
    enable_sparse: bool = True


class HybridQueryEngine:
    """QueryRequest -> processed query -> dense/sparse -> fusion -> filter -> rerank."""

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
        self.query_processor = query_processor
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.fusion = fusion
        self.metadata_filter = metadata_filter
        self.reranker = reranker
        self.config = config or HybridSearchConfig()

    def search(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        processed = self.query_processor.process(request, trace=trace)
        filters = _merge_filters(processed.filters, request.filters, request.collection)

        ranked_lists: list[list[RetrievalCandidate]] = []
        errors: JsonDict = {}

        if self.config.enable_dense and self.dense_retriever is not None:
            try:
                ranked_lists.append(
                    self.dense_retriever.retrieve(
                        processed.standalone_query,
                        top_k=self.config.dense_top_k,
                        filters=filters,
                        trace=trace,
                    )
                )
            except Exception as exc:
                errors["dense"] = str(exc)

        if self.config.enable_sparse and self.sparse_retriever is not None:
            try:
                ranked_lists.append(
                    self.sparse_retriever.retrieve(
                        processed.keywords,
                        top_k=self.config.sparse_top_k,
                        filters=filters,
                        trace=trace,
                    )
                )
            except Exception as exc:
                errors["sparse"] = str(exc)

        fused = self.fusion.fuse(
            [ranked for ranked in ranked_lists if ranked],
            top_k=max(request.top_k, self.config.fusion_top_k),
            trace=trace,
        )
        filtered = self.metadata_filter.apply(fused, filters, trace=trace)

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
        merged.setdefault("collection", collection)
    return merged
