"""Ports for building user-facing query responses."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import Citation, ImagePayload, QueryRequest, QueryResponse, RetrievalCandidate


@runtime_checkable
class CitationGenerator(Protocol):
    def generate(self, candidates: list[RetrievalCandidate]) -> list[Citation]: ...


@runtime_checkable
class MultimodalAssembler(Protocol):
    def resolve_images(
        self,
        candidates: list[RetrievalCandidate],
        include_images: bool,
    ) -> list[ImagePayload]: ...


@runtime_checkable
class ResponseBuilder(Protocol):
    def build(
        self,
        request: QueryRequest,
        candidates: list[RetrievalCandidate],
        trace: Any | None = None,
    ) -> QueryResponse: ...
