"""构建面向用户查询响应的端口契约。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import Citation, ImagePayload, QueryRequest, QueryResponse, RetrievalCandidate


@runtime_checkable
class CitationGenerator(Protocol):
    """从候选项生成引用记录。"""

    def generate(self, candidates: list[RetrievalCandidate]) -> list[Citation]: ...


@runtime_checkable
class MultimodalAssembler(Protocol):
    """从候选项组装多模态响应内容。"""

    def resolve_images(
        self,
        candidates: list[RetrievalCandidate],
        include_images: bool,
    ) -> list[ImagePayload]: ...


@runtime_checkable
class ResponseBuilder(Protocol):
    """把查询请求和候选项构建为领域响应。"""

    def build(
        self,
        request: QueryRequest,
        candidates: list[RetrievalCandidate],
        trace: Any | None = None,
    ) -> QueryResponse: ...
