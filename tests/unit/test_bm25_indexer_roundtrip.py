from __future__ import annotations

import hashlib
import math
import pickle
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from core.types import Chunk
from ingestion.embedding import SparseEncoder
from ingestion.storage import BM25Indexer
from ports.ingestion import BM25IndexStore


def make_chunk(
    chunk_id: str,
    text: str,
    *,
    source_path: str = "guide.md",
    collection: str = "docs",
    title: str | None = None,
) -> Chunk:
    metadata = {"source_path": source_path, "collection": collection}
    if title is not None:
        metadata["title"] = title
    return Chunk(
        id=chunk_id,
        text=text,
        metadata=metadata,
        source_ref=source_path,
        chunk_index=int(chunk_id.removeprefix("chunk-")),
    )


def statistics(chunks: list[Chunk]) -> list[dict]:
    return SparseEncoder().encode(chunks)


def test_build_persists_exact_idf_and_reloads_stable_query_order(tmp_path) -> None:
    chunks = [
        make_chunk("chunk-0", "python python vector"),
        make_chunk("chunk-1", "python storage"),
        make_chunk("chunk-2", "bm25 index"),
        make_chunk("chunk-3", "hybrid retrieval"),
        make_chunk("chunk-4", "other"),
    ]
    indexer = BM25Indexer(tmp_path / "bm25")

    indexer.build(chunks, statistics(chunks))

    expected_idf = math.log((5 - 2 + 0.5) / (2 + 0.5))
    assert indexer.inverted_index["python"]["idf"] == pytest.approx(expected_idf)
    assert indexer.inverted_index["python"]["postings"] == [
        {"chunk_id": "chunk-0", "tf": 2, "doc_length": 3},
        {"chunk_id": "chunk-1", "tf": 1, "doc_length": 2},
    ]
    assert indexer.index_path.is_file()

    reloaded = BM25Indexer(tmp_path / "bm25")
    hits = reloaded.query(["ＰＹＴＨＯＮ"], top_k=5)

    assert [hit.id for hit in hits] == ["chunk-0", "chunk-1"]
    assert hits[0].score > hits[1].score
    assert all(hit.score_kind == "bm25" for hit in hits)
    assert hits[0].text == "python python vector"


def test_title_is_searchable_but_returned_text_stays_unchanged(tmp_path) -> None:
    chunks = [
        make_chunk("chunk-0", "body only", title="Retrieval Guide"),
        make_chunk("chunk-1", "unrelated text"),
        make_chunk("chunk-2", "another document"),
    ]
    directory = tmp_path / "bm25"
    indexer = BM25Indexer(directory)
    indexer.build(chunks, statistics(chunks))

    hits = BM25Indexer(directory).query(["Retrieval Guide"], top_k=5)

    assert [hit.id for hit in hits] == ["chunk-0"]
    assert hits[0].text == "body only"


def test_upsert_replaces_existing_chunk_and_preserves_other_documents(tmp_path) -> None:
    initial = [make_chunk("chunk-0", "alpha"), make_chunk("chunk-1", "beta")]
    indexer = BM25Indexer(tmp_path / "bm25")
    indexer.build(initial, statistics(initial))
    changed = make_chunk("chunk-1", "delta delta")
    added = make_chunk("chunk-2", "epsilon")

    indexer.upsert([changed, added], statistics([changed, added]))

    reloaded = BM25Indexer(tmp_path / "bm25")
    assert [hit.id for hit in reloaded.query(["alpha"], top_k=5)] == ["chunk-0"]
    assert reloaded.query(["beta"], top_k=5) == []
    assert [hit.id for hit in reloaded.query(["delta"], top_k=5)] == ["chunk-1"]
    assert [hit.id for hit in reloaded.query(["epsilon"], top_k=5)] == ["chunk-2"]


def test_build_replaces_the_previous_corpus(tmp_path) -> None:
    old = make_chunk("chunk-0", "legacy")
    new = make_chunk("chunk-1", "current")
    indexer = BM25Indexer(tmp_path / "bm25")
    indexer.build([old], statistics([old]))

    indexer.build([new], statistics([new]))

    assert indexer.query(["legacy"], top_k=5) == []
    assert [hit.id for hit in indexer.query(["current"], top_k=5)] == ["chunk-1"]


def test_query_filters_metadata_and_remove_document_is_collection_scoped(tmp_path) -> None:
    chunks = [
        make_chunk("chunk-0", "shared target", source_path="same.md", collection="docs"),
        make_chunk("chunk-1", "shared target", source_path="same.md", collection="other"),
        make_chunk("chunk-2", "shared target", source_path="keep.md", collection="docs"),
    ]
    indexer = BM25Indexer(tmp_path / "bm25")
    indexer.build(chunks, statistics(chunks))

    filtered = indexer.query(["target"], top_k=5, filters={"collection": "docs"})
    assert [hit.id for hit in filtered] == ["chunk-0", "chunk-2"]

    indexer.remove_document("same.md", "docs")

    reloaded = BM25Indexer(tmp_path / "bm25")
    assert reloaded.query(
        ["target"], top_k=5, filters={"source_path": "same.md", "collection": "docs"}
    ) == []
    assert [
        hit.id
        for hit in reloaded.query(["target"], top_k=5, filters={"collection": "other"})
    ] == ["chunk-1"]


def test_rejects_misaligned_or_invalid_sparse_statistics(tmp_path) -> None:
    chunk = make_chunk("chunk-0", "alpha")
    indexer = BM25Indexer(tmp_path / "bm25")

    with pytest.raises(ValueError, match="count must match"):
        indexer.build([chunk], [])
    with pytest.raises(ValueError, match="duplicate chunk id"):
        indexer.build([chunk, chunk], statistics([chunk, chunk]))
    with pytest.raises(ValueError, match="doc_length must equal total term frequency"):
        indexer.build([chunk], [{"terms": {"alpha": 1}, "doc_length": 2}])
    with pytest.raises(ValueError, match="term frequency must be a positive integer"):
        indexer.build([chunk], [{"terms": {"alpha": 0}, "doc_length": 0}])


def test_empty_and_invalid_queries_have_explicit_behavior(tmp_path) -> None:
    indexer = BM25Indexer(tmp_path / "bm25")
    indexer.build([], [])

    assert indexer.query([], top_k=5) == []
    assert indexer.query(["---"], top_k=5) == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        indexer.query(["alpha"], top_k=0)


def test_corrupt_snapshot_fails_with_context(tmp_path) -> None:
    directory = tmp_path / "bm25"
    directory.mkdir()
    (directory / "index.pkl").write_bytes(b"not a pickle")

    with pytest.raises(ValueError, match="bm25 index load error"):
        BM25Indexer(directory)


def test_old_snapshot_requires_reingestion(tmp_path) -> None:
    directory = tmp_path / "bm25"
    chunk = make_chunk("chunk-0", "alpha")
    indexer = BM25Indexer(directory)
    indexer.build([chunk], statistics([chunk]))
    with indexer.index_path.open("rb") as handle:
        snapshot = pickle.load(handle)
    snapshot["version"] = 1
    with indexer.index_path.open("wb") as handle:
        pickle.dump(snapshot, handle)

    with pytest.raises(ValueError, match="re-ingest all documents"):
        BM25Indexer(directory)


def test_bm25_indexer_matches_ingestion_protocol(tmp_path) -> None:
    assert isinstance(BM25Indexer(tmp_path / "bm25"), BM25IndexStore)


def test_two_indexer_instances_do_not_lose_concurrent_updates(tmp_path) -> None:
    directory = tmp_path / "bm25"
    first_indexer = BM25Indexer(directory)
    second_indexer = BM25Indexer(directory)
    barrier = Barrier(2)

    def write(indexer: BM25Indexer, chunk: Chunk) -> None:
        barrier.wait()
        indexer.upsert([chunk], statistics([chunk]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write, first_indexer, make_chunk("chunk-0", "alpha")),
            pool.submit(write, second_indexer, make_chunk("chunk-1", "beta")),
        ]
        for future in futures:
            future.result()

    reloaded = BM25Indexer(directory)
    assert [hit.id for hit in reloaded.query(["alpha"], top_k=5)] == ["chunk-0"]
    assert [hit.id for hit in reloaded.query(["beta"], top_k=5)] == ["chunk-1"]


def test_offline_export_returns_only_final_active_chunk_records(tmp_path) -> None:
    doc_key = "d" * 64

    class _Active:
        def get_active_generations(self, collection: str | None = None) -> dict[str, int]:
            return {doc_key: 2}

    def final_chunk(text: str, generation: int) -> Chunk:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        suffix = hashlib.sha256(f"0\0{content_hash}".encode()).hexdigest()[:32]
        return Chunk(
            id=f"{doc_key}:{generation}:{suffix}",
            text=text,
            metadata={
                "source_path": "guide.md",
                "collection": "docs",
                "doc_key": doc_key,
                "generation": generation,
                "chunk_index": 0,
            },
            source_ref="guide.md",
            chunk_index=0,
        )

    old = final_chunk("old text", 1)
    current = final_chunk("current text", 2)
    indexer = BM25Indexer(tmp_path / "bm25", generation_store=_Active())  # type: ignore[arg-type]
    indexer.build([old, current], statistics([old, current]))

    records = indexer.list_active_chunk_records("docs")

    assert [record.id for record in records] == [current.id]
    assert records[0].text == "current text"
    assert records[0].content_hash == hashlib.sha256(b"current text").hexdigest()
