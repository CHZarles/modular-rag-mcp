"""Tests for C1: core data types (Document / Chunk / ChunkRecord / ...)."""

from __future__ import annotations

from src.core.types import (
    Chunk,
    ChunkRecord,
    CollectionInfo,
    DeleteResult,
    Document,
    DocumentSummary,
    ImagePayload,
    ImageRef,
    IngestionRequest,
    IngestionResult,
    ProcessedQuery,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
    SearchHit,
)


def test_document_round_trips_to_dict() -> None:
    doc = Document(
        id="d1",
        text="hello",
        metadata={"source_path": "a.pdf", "collection": "docs"},
    )
    d = doc.to_dict()
    assert d["id"] == "d1"
    assert d["text"] == "hello"
    assert d["metadata"]["source_path"] == "a.pdf"


def test_chunk_carries_offsets_and_source_ref() -> None:
    chunk = Chunk(
        id="c1",
        text="abc",
        metadata={"source_path": "a.pdf"},
        source_ref="d1",
        chunk_index=0,
        start_offset=0,
        end_offset=3,
    )
    assert chunk.start_offset == 0
    assert chunk.end_offset == 3
    assert chunk.source_ref == "d1"
    assert chunk.chunk_index == 0


def test_chunkrecord_to_from_chunk_preserves_text_and_metadata() -> None:
    chunk = Chunk(
        id="c2",
        text="payload",
        metadata={"source_path": "b.pdf", "collection": "docs"},
        source_ref="d1",
        chunk_index=1,
        start_offset=10,
        end_offset=17,
    )
    rec = ChunkRecord.from_chunk(chunk, dense_vector=[0.1, 0.2])
    assert rec.id == "c2"
    assert rec.text == "payload"
    assert rec.dense_vector == [0.1, 0.2]
    restored = ChunkRecord.from_dict(rec.to_dict())
    assert restored.id == "c2" and restored.text == "payload"


def test_imageref_accepts_id_alias() -> None:
    img = ImageRef.from_dict(
        {"id": "img-1", "path": "img.png", "collection": "docs", "source_path": "a.pdf"}
    )
    assert img.image_id == "img-1"
    assert img.path == "img.png"


def test_query_response_round_trip_nested_contracts() -> None:
    response = QueryResponse(
        answer="a",
        citations=[],
        items=[],
        images=[ImagePayload(image_id="i1", mime_type="image/png", uri="i1.png")],
        request_id="r1",
    )
    restored = QueryResponse.from_dict(response.to_dict())
    assert restored.images[0].image_id == "i1"
    assert restored.request_id == "r1"


def test_query_request_carries_collection() -> None:
    req = QueryRequest(query="q", collection="docs", top_k=5)
    assert req.collection == "docs"
    assert req.top_k == 5


def test_processed_query_defaults_filters_to_empty_dict() -> None:
    pq = ProcessedQuery(original_query="q", standalone_query="q", keywords=["k"])
    assert pq.filters == {}


def test_retrieval_candidate_round_trip() -> None:
    cand = RetrievalCandidate(
        chunk_id="c", text="t", metadata={}, score=1.0, source="dense", rank=1
    )
    restored = RetrievalCandidate.from_dict(cand.to_dict())
    assert restored.source == "dense"
    assert restored.rank == 1


def test_searchhit_score_kind_is_string() -> None:
    hit = SearchHit(id="x", text="t", metadata={}, score=0.5, score_kind="similarity")
    assert hit.score_kind == "similarity"


def test_ingestion_request_and_result_round_trip() -> None:
    req = IngestionRequest(source_path="a.pdf", collection="docs", force=False)
    res = IngestionResult(
        source_path="a.pdf",
        collection="docs",
        status="success",
        file_hash="abc",
        chunk_count=3,
        image_count=1,
    )
    assert req.force is False
    assert res.chunk_count == 3
    assert res.status == "success"


def test_collection_info_round_trip() -> None:
    info = CollectionInfo(name="docs", document_count=2, chunk_count=10)
    restored = CollectionInfo.from_dict(info.to_dict())
    assert restored.name == "docs"
    assert restored.chunk_count == 10


def test_delete_result_round_trip() -> None:
    res = DeleteResult(
        source_path="a.pdf",
        collection="docs",
        deleted_chunks=5,
        deleted_images=2,
        removed_bm25=True,
        removed_integrity_record=True,
    )
    restored = DeleteResult.from_dict(res.to_dict())
    assert restored.deleted_chunks == 5
    assert restored.deleted_images == 2
    assert restored.removed_bm25 is True


def test_document_summary_round_trip() -> None:
    summary = DocumentSummary(
        doc_id="d1",
        source_path="a.pdf",
        title="Doc 1",
        summary="Brief",
        tags=["t1"],
    )
    restored = DocumentSummary.from_dict(summary.to_dict())
    assert restored.title == "Doc 1"
    assert restored.tags == ["t1"]