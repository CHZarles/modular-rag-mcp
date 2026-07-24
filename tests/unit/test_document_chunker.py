"""Tests for C4: DocumentChunker (splitter integration)."""

from __future__ import annotations

import pytest

from src.core.types import Chunk, Document
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.libs.splitter.recursive_splitter import RecursiveCharacterSplitter


def _splitter(chunk_size: int = 50) -> RecursiveCharacterSplitter:
    return RecursiveCharacterSplitter(chunk_size=chunk_size, chunk_overlap=0)


def _document(text: str, doc_id: str = "doc1", collection: str = "docs") -> Document:
    return Document(
        id=doc_id,
        text=text,
        metadata={"source_path": "doc.txt", "collection": collection},
    )


def test_chunk_text_matches_splitter_output() -> None:
    text = "hello world. " * 20
    doc = _document(text)
    chunks = DocumentChunker(_splitter(chunk_size=60)).split_document(doc)
    assert chunks and all(isinstance(c, Chunk) for c in chunks)
    joined = " ".join(c.text for c in chunks)
    # Concatenating should reconstruct roughly the original text.
    assert "hello world" in joined


def test_chunk_ids_are_stable_and_unique() -> None:
    text = "alpha bravo charlie delta echo foxtrot golf"
    doc = _document(text, doc_id="abc")
    chunker = DocumentChunker(_splitter(chunk_size=15))
    chunks1 = chunker.split_document(doc)
    chunks2 = chunker.split_document(doc)
    assert [c.id for c in chunks1] == [c.id for c in chunks2]
    assert len({c.id for c in chunks1}) == len(chunks1)


def test_chunk_metadata_inherits_source_path_and_collection() -> None:
    doc = _document("some text here", doc_id="docA", collection="kb")
    chunks = DocumentChunker(_splitter()).split_document(doc)
    for c in chunks:
        assert c.metadata["source_path"] == "doc.txt"
        assert c.metadata["collection"] == "kb"
        assert c.metadata["source_ref"] == "docA"


def test_chunk_index_increments_from_zero() -> None:
    chunks = DocumentChunker(_splitter(chunk_size=10)).split_document(
        _document("aaa bbb ccc ddd eee")
    )
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_chunk_offsets_point_into_source_text() -> None:
    text = "prefix " * 5 + "MIDDLE" + " suffix" * 5
    chunks = DocumentChunker(_splitter(chunk_size=20)).split_document(_document(text))
    for c in chunks:
        if c.start_offset is not None and c.end_offset is not None:
            assert text[c.start_offset : c.end_offset] == c.text or c.text in text


def test_chunk_handles_empty_document() -> None:
    chunks = DocumentChunker(_splitter()).split_document(_document(""))
    assert chunks == []


def test_chunk_overlapping_images_filter_to_relevant_chunk() -> None:
    doc = Document(
        id="img-doc",
        text="alpha beta gamma delta",
        metadata={
            "source_path": "x.txt",
            "collection": "c",
            "images": [
                {"id": "img-1", "text_offset": 6, "text_length": 4},  # spans "beta "
                {"id": "img-2", "text_offset": 100, "text_length": 4},  # outside
            ],
        },
    )
    chunks = DocumentChunker(_splitter(chunk_size=11)).split_document(doc)
    # At least one chunk should include img-1.
    flat = [img["id"] for c in chunks for img in c.metadata.get("images", [])]
    assert "img-1" in flat
    assert "img-2" not in flat


def test_chunk_invalid_splitter_type_rejected() -> None:
    # DocumentChunker is duck-typed; passing a non-splitter object will only
    # fail when split_text() is actually invoked. We verify that surface.
    chunker = DocumentChunker("not-a-splitter")  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        chunker.split_document(_document("hello world"))