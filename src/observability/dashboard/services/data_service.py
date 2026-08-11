"""Dashboard facade for browsing active indexed documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.settings import Settings
from src.core.types import CollectionInfo, DeleteResult, DocumentSummary, JsonDict
from src.ingestion import DocumentManager
from src.ingestion.storage import BM25Indexer, ImageStorage, SQLiteGrepIndex
from src.libs.loader import SQLiteIntegrityStore
from src.libs.vector_store import ChromaStore


class DataService:
    """Expose document browsing without leaking storage assembly into API handlers."""

    def __init__(self, document_manager: DocumentManager) -> None:
        self.document_manager = document_manager

    @classmethod
    def from_settings(cls, settings: Settings) -> DataService:
        storage = settings.ingestion.get("storage")
        if not isinstance(storage, Mapping):
            raise ValueError("Missing required setting: ingestion.storage")

        integrity = SQLiteIntegrityStore(
            _required_text(storage, "integrity_db_path", "ingestion.storage")
        )
        chroma = ChromaStore(settings.vector_store)
        bm25 = BM25Indexer(
            _required_text(storage, "bm25_path", "ingestion.storage"),
            generation_store=integrity,
        )
        images = ImageStorage(
            _required_text(storage, "image_db_path", "ingestion.storage"),
            _required_text(storage, "image_root", "ingestion.storage"),
            generation_store=integrity,
        )
        grep_index = None
        if settings.grep.get("enabled", False):
            grep_timeout_ms = settings.grep.get("timeout_ms", 1000)
            if not isinstance(grep_timeout_ms, int) or isinstance(grep_timeout_ms, bool):
                raise ValueError("Setting grep.timeout_ms must be a positive integer")
            grep_index = SQLiteGrepIndex(
                _required_text(settings.grep, "db_path", "grep"),
                timeout_ms=grep_timeout_ms,
            )
        return cls(DocumentManager(chroma, bm25, images, integrity, grep_index))

    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]:
        return self.document_manager.list_documents(collection)

    def list_collections(self) -> list[str]:
        return sorted(
            {
                str(document.metadata["collection"])
                for document in self.list_documents()
                if document.metadata.get("collection")
            },
            key=str.casefold,
        )

    def get_document_detail(self, doc_id: str) -> JsonDict:
        return self.document_manager.get_document_detail(doc_id)

    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo:
        return self.document_manager.get_collection_stats(collection)

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        return self.document_manager.delete_document(source_path, collection)


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


__all__ = ["DataService"]
