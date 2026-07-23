"""Ports used by the ingestion pipeline."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import Chunk, ChunkRecord, Document, ImageRef, JsonDict, SearchHit


@runtime_checkable
class FileIntegrityStore(Protocol):
    def compute_sha256(self, source_path: str) -> str: ...
    def should_skip(self, file_hash: str, collection: str) -> bool: ...
    def mark_processing(self, file_hash: str, source_path: str, collection: str) -> None: ...
    def mark_success(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        chunk_count: int,
    ) -> None: ...
    def mark_failed(self, file_hash: str, source_path: str, collection: str, error: str) -> None: ...
    def remove_record(self, file_hash: str, collection: str) -> None: ...
    def list_processed(self, collection: str | None = None) -> list[JsonDict]: ...


@runtime_checkable
class BaseLoader(Protocol):
    supported_extensions: tuple[str, ...]

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document: ...


@runtime_checkable
class BaseSplitter(Protocol):
    def split_text(self, text: str, trace: Any | None = None) -> list[str]: ...


@runtime_checkable
class DocumentChunker(Protocol):
    def split_document(self, document: Document, trace: Any | None = None) -> list[Chunk]: ...


@runtime_checkable
class BaseTransform(Protocol):
    name: str

    def transform(self, chunks: list[Chunk], trace: Any | None = None) -> list[Chunk]: ...


@runtime_checkable
class BaseEmbedding(Protocol):
    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]: ...


@runtime_checkable
class SparseEncoder(Protocol):
    def encode(self, chunks: list[Chunk], trace: Any | None = None) -> list[JsonDict]: ...


@runtime_checkable
class BaseVectorStore(Protocol):
    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None: ...
    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]: ...
    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]: ...
    def delete_by_metadata(self, filters: JsonDict) -> int: ...


@runtime_checkable
class BM25IndexStore(Protocol):
    def upsert(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[JsonDict],
        trace: Any | None = None,
    ) -> None: ...
    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]: ...
    def remove_document(self, source_path: str, collection: str) -> None: ...


@runtime_checkable
class ImageStore(Protocol):
    def save_refs(self, images: list[ImageRef], trace: Any | None = None) -> None: ...
    def get(self, image_id: str) -> ImageRef | None: ...
    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]: ...
    def delete_by_document(self, source_path: str, collection: str) -> int: ...
