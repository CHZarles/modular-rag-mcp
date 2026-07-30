from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.query_engine import DenseRetriever
from core.types import Chunk, ChunkRecord, ImageRef, SearchHit
from ingestion.embedding import SparseEncoder
from ingestion.storage import BM25Indexer, ImageStorage


class ActiveGenerations:
    def __init__(self, values: dict[str, int]) -> None:
        self.values = values

    def get_active_generations(self, collection: str | None = None) -> dict[str, int]:
        return dict(self.values)


class FakeEmbedding:
    def embed(self, texts: list[str], trace: object | None = None) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeVectorStore:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.requested_top_k = 0

    def upsert(self, records: list[ChunkRecord], trace: object | None = None) -> None:
        return None

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
        trace: object | None = None,
    ) -> list[SearchHit]:
        self.requested_top_k = top_k
        return self.hits[:top_k]

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return []

    def delete_by_metadata(self, filters: dict) -> int:
        return 0


def _chunk(chunk_id: str, text: str, doc_key: str, generation: int) -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={
            "source_path": "manual.pdf",
            "collection": "docs",
            "doc_key": doc_key,
            "generation": generation,
        },
        source_ref="manual.pdf",
        chunk_index=0,
    )


def test_dense_retriever_discards_inactive_generations_and_overfetches() -> None:
    doc_key = "d" * 64
    hits = [
        SearchHit("old", "old", {"doc_key": doc_key, "generation": 8}, 0.01, "distance"),
        SearchHit("new", "new", {"doc_key": doc_key, "generation": 9}, 0.02, "distance"),
    ]
    vector_store = FakeVectorStore(hits)
    retriever = DenseRetriever(
        FakeEmbedding(),
        vector_store,
        generation_store=ActiveGenerations({doc_key: 9}),
        overfetch_factor=5,
    )

    candidates = retriever.retrieve("query", top_k=1, filters={"collection": "docs"})

    assert [candidate.chunk_id for candidate in candidates] == ["new"]
    assert vector_store.requested_top_k == 5


def test_bm25_statistics_and_hits_use_only_active_generations(tmp_path: Path) -> None:
    doc_key = "d" * 64
    old = _chunk("old", "legacykeyword shared", doc_key, 8)
    new = _chunk("new", "currentkeyword shared", doc_key, 9)
    other = _chunk("other", "another document", "e" * 64, 1)
    chunks = [old, new, other]
    indexer = BM25Indexer(
        tmp_path / "bm25",
        generation_store=ActiveGenerations({doc_key: 9, "e" * 64: 1}),
    )
    indexer.build(chunks, SparseEncoder().encode(chunks))

    assert indexer.query(["legacykeyword"], top_k=5) == []
    assert [hit.id for hit in indexer.query(["currentkeyword"], top_k=5)] == ["new"]


def test_image_queries_return_only_active_generation(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    collection_root = image_root / "docs"
    collection_root.mkdir(parents=True)
    image_path = collection_root / "shared.png"
    image_path.write_bytes(b"png")
    doc_key = "d" * 64
    image = ImageRef(
        image_id="shared",
        path=str(image_path),
        collection="docs",
        source_path="manual.pdf",
    )
    state = ActiveGenerations({doc_key: 9})
    store = ImageStorage(tmp_path / "images.db", image_root, generation_store=state)

    store.save_refs([image], doc_key, 8)
    store.save_refs([replace(image, page=2)], doc_key, 9)

    active = store.list_by_document("manual.pdf", "docs")
    assert len(active) == 1
    assert active[0].page == 2
    assert store.delete_generation(doc_key, 8) == 1
