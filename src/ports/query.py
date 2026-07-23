"""Ports for query processing, retrieval, fusion, filtering, and reranking."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import JsonDict, ProcessedQuery, QueryRequest, RetrievalCandidate


@runtime_checkable
class QueryProcessor(Protocol):
    def process(self, request: QueryRequest, trace: Any | None = None) -> ProcessedQuery: ...


@runtime_checkable
class DenseRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class SparseRetriever(Protocol):
    def retrieve(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class FusionStrategy(Protocol):
    def fuse(
        self,
        ranked_lists: list[list[RetrievalCandidate]],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class MetadataFilter(Protocol):
    def apply(
        self,
        candidates: list[RetrievalCandidate],
        filters: JsonDict,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class BaseReranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class QueryEngine(Protocol):
    def search(
        self,
        request: QueryRequest,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...
