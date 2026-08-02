from __future__ import annotations

from pathlib import Path

from core.settings import Settings
from core.types import CollectionInfo, DocumentSummary, JsonDict
from observability.dashboard.services import DataService


class FakeDocumentManager:
    def __init__(self) -> None:
        self.documents = [
            DocumentSummary(
                doc_id="doc-b",
                source_path="/tmp/b.pdf",
                metadata={"collection": "notes", "chunk_count": 1, "image_count": 0},
            ),
            DocumentSummary(
                doc_id="doc-a",
                source_path="/tmp/a.pdf",
                metadata={"collection": "Docs", "chunk_count": 2, "image_count": 1},
            ),
        ]
        self.detail: JsonDict = {"document": {"doc_id": "doc-a"}, "chunks": [], "images": []}

    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]:
        if collection is None:
            return list(self.documents)
        return [
            document
            for document in self.documents
            if document.metadata.get("collection") == collection
        ]

    def get_document_detail(self, doc_id: str) -> JsonDict:
        assert doc_id == "doc-a"
        return self.detail

    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo:
        documents = self.list_documents(collection)
        return CollectionInfo(
            name=collection or "all",
            document_count=len(documents),
            chunk_count=sum(int(item.metadata["chunk_count"]) for item in documents),
            image_count=sum(int(item.metadata["image_count"]) for item in documents),
        )


def test_data_service_delegates_document_views_and_sorts_collections() -> None:
    manager = FakeDocumentManager()
    service = DataService(manager)  # type: ignore[arg-type]

    assert service.list_collections() == ["Docs", "notes"]
    assert [item.doc_id for item in service.list_documents("Docs")] == ["doc-a"]
    assert service.get_document_detail("doc-a") is manager.detail
    assert service.get_collection_stats("notes").chunk_count == 1


def test_data_service_builds_empty_local_stores_from_settings(tmp_path: Path) -> None:
    service = DataService.from_settings(_settings(tmp_path))

    assert service.list_documents() == []
    assert service.list_collections() == []
    assert service.get_collection_stats().document_count == 0


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={
            "backend": "chroma",
            "persist_path": str(tmp_path / "chroma"),
            "collection_name": "dashboard-data-test",
            "distance_metric": "cosine",
        },
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
        ingestion={
            "storage": {
                "integrity_db_path": str(tmp_path / "integrity.db"),
                "bm25_path": str(tmp_path / "bm25"),
                "image_db_path": str(tmp_path / "images.db"),
                "image_root": str(tmp_path / "images"),
            }
        },
    )
