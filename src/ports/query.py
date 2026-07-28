"""查询预处理、召回、融合、过滤与重排序端口。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import JsonDict, ProcessedQuery, QueryRequest, RetrievalCandidate


@runtime_checkable
class QueryProcessor(Protocol):
    """把原始请求转换为可执行查询。"""

    def process(self, request: QueryRequest, trace: Any | None = None) -> ProcessedQuery: ...


@runtime_checkable
class DenseRetriever(Protocol):
    """按语义向量召回候选项。"""

    def retrieve(
        self,
        query: str,
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class SparseRetriever(Protocol):
    """按关键词稀疏索引召回候选项。"""

    def retrieve(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class FusionStrategy(Protocol):
    """把多路有序候选列表融合为统一排名。"""

    def fuse(
        self,
        ranked_lists: list[list[RetrievalCandidate]],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class MetadataFilter(Protocol):
    """根据结构化元数据约束筛选候选项。"""

    def apply(
        self,
        candidates: list[RetrievalCandidate],
        filters: JsonDict,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class BaseReranker(Protocol):
    """对融合后的候选集进行最终重排序。"""

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class QueryEngine(Protocol):
    """对外提供完整查询链路的统一入口。"""

    def search(
        self,
        request: QueryRequest,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]: ...
