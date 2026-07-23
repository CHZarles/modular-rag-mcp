"""Stable domain contracts for the modular RAG system.

These types are intentionally small and provider-neutral. Concrete adapters
can store extra details in ``metadata`` or ``debug`` without leaking backend
objects across module boundaries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

JsonDict = dict[str, Any]
Metadata = dict[str, Any]
Vector = list[float]
SparseVector = JsonDict
ProgressCallback = Callable[[str, int, int], None]


class SerializableDataclass:
    """Mixin for JSON-shaped dataclasses."""

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ImageRef(SerializableDataclass):
    image_id: str
    path: str
    collection: str
    source_path: str
    page: int | None = None
    mime_type: str = "image/png"
    text_offset: int | None = None
    text_length: int | None = None
    position: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ImageRef":
        payload = dict(data)
        if "image_id" not in payload and "id" in payload:
            payload["image_id"] = payload.pop("id")
        return cls(**payload)


@dataclass(frozen=True)
class Document(SerializableDataclass):
    id: str
    text: str
    metadata: Metadata

    @classmethod
    def from_dict(cls, data: JsonDict) -> "Document":
        return cls(**data)


@dataclass(frozen=True)
class Chunk(SerializableDataclass):
    id: str
    text: str
    metadata: Metadata
    source_ref: str
    chunk_index: int
    start_offset: int | None = None
    end_offset: int | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> "Chunk":
        return cls(**data)


@dataclass(frozen=True)
class ChunkRecord(SerializableDataclass):
    id: str
    text: str
    metadata: Metadata
    dense_vector: list[float] | None = None
    sparse_vector: JsonDict | None = None
    content_hash: str | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ChunkRecord":
        return cls(**data)

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        dense_vector: list[float] | None = None,
        sparse_vector: JsonDict | None = None,
        content_hash: str | None = None,
    ) -> "ChunkRecord":
        return cls(
            id=chunk.id,
            text=chunk.text,
            metadata=dict(chunk.metadata),
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            content_hash=content_hash,
        )


@dataclass(frozen=True)
class ProcessedQuery(SerializableDataclass):
    original_query: str
    standalone_query: str
    keywords: list[str]
    filters: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ProcessedQuery":
        return cls(**data)


@dataclass(frozen=True)
class SearchHit(SerializableDataclass):
    id: str
    text: str
    metadata: Metadata
    score: float
    score_kind: Literal["similarity", "distance", "bm25", "unknown"] = "unknown"
    raw: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "SearchHit":
        return cls(**data)


@dataclass(frozen=True)
class RetrievalCandidate(SerializableDataclass):
    chunk_id: str
    text: str
    metadata: Metadata
    score: float
    source: Literal["dense", "sparse", "fusion", "rerank"]
    rank: int
    debug: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "RetrievalCandidate":
        return cls(**data)


@dataclass(frozen=True)
class Citation(SerializableDataclass):
    citation_id: str
    chunk_id: str
    source_path: str
    page: int | None
    text: str
    score: float
    metadata: Metadata = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "Citation":
        return cls(**data)


@dataclass(frozen=True)
class ImagePayload(SerializableDataclass):
    image_id: str
    mime_type: str
    data_base64: str | None = None
    uri: str | None = None
    source_ref: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ImagePayload":
        return cls(**data)


@dataclass(frozen=True)
class QueryRequest(SerializableDataclass):
    query: str
    top_k: int = 5
    collection: str = "default"
    filters: JsonDict = field(default_factory=dict)
    include_images: bool = True
    request_id: str | None = None

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")

    @classmethod
    def from_dict(cls, data: JsonDict) -> "QueryRequest":
        return cls(**data)


@dataclass(frozen=True)
class QueryResponse(SerializableDataclass):
    answer: str
    citations: list[Citation]
    items: list[RetrievalCandidate]
    images: list[ImagePayload] = field(default_factory=list)
    request_id: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "QueryResponse":
        payload = dict(data)
        payload["citations"] = [
            item if isinstance(item, Citation) else Citation.from_dict(item)
            for item in payload.get("citations", [])
        ]
        payload["items"] = [
            item if isinstance(item, RetrievalCandidate) else RetrievalCandidate.from_dict(item)
            for item in payload.get("items", [])
        ]
        payload["images"] = [
            item if isinstance(item, ImagePayload) else ImagePayload.from_dict(item)
            for item in payload.get("images", [])
        ]
        return cls(**payload)


@dataclass(frozen=True)
class IngestionRequest(SerializableDataclass):
    source_path: str
    collection: str = "default"
    force: bool = False
    request_id: str | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> "IngestionRequest":
        return cls(**data)


@dataclass(frozen=True)
class IngestionResult(SerializableDataclass):
    source_path: str
    collection: str
    status: Literal["success", "skipped", "failed"]
    file_hash: str
    document_id: str | None = None
    chunk_count: int = 0
    image_count: int = 0
    error: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "IngestionResult":
        return cls(**data)


@dataclass(frozen=True)
class CollectionInfo(SerializableDataclass):
    name: str
    document_count: int
    chunk_count: int
    image_count: int = 0
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "CollectionInfo":
        return cls(**data)


@dataclass(frozen=True)
class DocumentSummary(SerializableDataclass):
    doc_id: str
    source_path: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "DocumentSummary":
        return cls(**data)


@dataclass(frozen=True)
class DeleteResult(SerializableDataclass):
    source_path: str
    collection: str
    deleted_chunks: int
    deleted_images: int
    removed_bm25: bool
    removed_integrity_record: bool
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "DeleteResult":
        return cls(**data)


@dataclass(frozen=True)
class EvaluationCase(SerializableDataclass):
    case_id: str
    query: str
    expected_chunk_ids: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "EvaluationCase":
        return cls(**data)


@dataclass(frozen=True)
class EvaluationReport(SerializableDataclass):
    run_id: str
    metrics: dict[str, float]
    cases: list[JsonDict]
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "EvaluationReport":
        return cls(**data)
