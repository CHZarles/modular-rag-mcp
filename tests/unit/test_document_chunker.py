from __future__ import annotations

import hashlib
from typing import Any

from core.types import Document
from ingestion.chunking import DocumentChunker


class FakeSplitter:
    """返回预设文本块，隔离 DocumentChunker 与具体切分算法。"""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.received_trace: Any | None = None

    def split_text(self, text: str, trace: Any | None = None) -> list[str]:
        self.received_trace = trace
        return list(self.chunks)


def test_document_chunker_builds_stable_domain_chunks() -> None:
    texts = ["# Title\n\nAlpha", "Beta"]
    splitter = FakeSplitter(texts)
    chunker = DocumentChunker(splitter=splitter)
    document = Document(
        id="doc-1",
        text="# Title\n\nAlpha\n\nBeta",
        metadata={
            "source_path": "docs/example.pdf",
            "collection": "docs",
            "doc_type": "pdf",
            "title": "example",
        },
    )
    trace = object()

    first = chunker.split_document(document, trace=trace)
    second = chunker.split_document(document, trace=trace)

    expected_ids = [
        f"doc-1_{index:04d}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"
        for index, text in enumerate(texts)
    ]
    assert [chunk.id for chunk in first] == expected_ids
    assert [chunk.id for chunk in second] == expected_ids
    assert len(set(expected_ids)) == len(expected_ids)
    assert splitter.received_trace is trace
    assert first[0].source_ref == document.id
    assert first[0].chunk_index == 0
    assert first[0].metadata == {**document.metadata, "chunk_index": 0}
    assert first[0].start_offset == 0
    assert first[0].end_offset == len(texts[0])


def test_document_chunker_distributes_only_referenced_images() -> None:
    image_1 = {"id": "img-1", "path": "images/img-1.png"}
    image_2 = {"id": "img-2", "path": "images/img-2.png"}
    texts = [
        "Intro\n\n[IMAGE: img-1]",
        "Middle\n\n[IMAGE: img-2]",
        "End",
    ]
    document = Document(
        id="doc-images",
        text="\n\n".join(texts),
        metadata={"source_path": "images.pdf", "images": [image_1, image_2]},
    )

    chunks = DocumentChunker(splitter=FakeSplitter(texts)).split_document(document)

    assert chunks[0].metadata["images"] == [image_1]
    assert chunks[0].metadata["image_refs"] == ["img-1"]
    assert chunks[1].metadata["images"] == [image_2]
    assert chunks[1].metadata["image_refs"] == ["img-2"]
    assert "images" not in chunks[2].metadata
    assert "image_refs" not in chunks[2].metadata
    assert document.metadata["images"] == [image_1, image_2]


def test_document_chunker_locates_overlapping_splitter_output() -> None:
    document = Document(
        id="doc-overlap",
        text="abc def ghi jkl",
        metadata={"source_path": "overlap.pdf"},
    )
    splitter = FakeSplitter(["abc def ghi", "def ghi jkl"])

    chunks = DocumentChunker(splitter=splitter).split_document(document)

    assert [(chunk.start_offset, chunk.end_offset) for chunk in chunks] == [(0, 11), (4, 15)]


def test_document_chunker_uses_splitter_settings() -> None:
    text = "\n\n".join(f"Paragraph {index} has reusable retrieval text." for index in range(12))
    document = Document(id="doc-config", text=text, metadata={"source_path": "config.pdf"})

    small_chunks = DocumentChunker(
        {"splitter": {"provider": "recursive", "chunk_size": 80, "chunk_overlap": 0}}
    ).split_document(document)
    large_chunks = DocumentChunker(
        {"splitter": {"provider": "recursive", "chunk_size": 240, "chunk_overlap": 0}}
    ).split_document(document)

    assert len(small_chunks) > len(large_chunks)
