"""Tests for C6 + C7: MetadataEnricher and ImageCaptioner."""

from __future__ import annotations

from src.core.types import Chunk
from src.ingestion.transform.image_captioner import ImageCaptioner
from src.ingestion.transform.metadata_enricher import MetadataEnricher
from src.ports.llm import BaseVisionLLM, ChatResponse, ImageInput


def _chunk(text: str, cid: str = "c1") -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata={"source_path": "x.txt", "collection": "docs"},
        source_ref="d1",
        chunk_index=0,
    )


def test_metadata_enricher_extracts_title_from_heading() -> None:
    chunks = [_chunk("# My Title\n\nSome body text here.")]
    out = MetadataEnricher().transform(chunks)
    assert out[0].metadata["title"] == "My Title"


def test_metadata_enricher_falls_back_to_first_line() -> None:
    chunks = [_chunk("First line here.\n\nMore content.")]
    out = MetadataEnricher().transform(chunks)
    assert out[0].metadata["title"] == "First line here."


def test_metadata_enricher_extracts_summary() -> None:
    chunks = [_chunk("Short sentence. Another one. Yet more text.")]
    out = MetadataEnricher().transform(chunks)
    assert "Short sentence" in out[0].metadata["summary"]


def test_metadata_enricher_extracts_tags() -> None:
    chunks = [_chunk("RAG retrieval augmented generation with hybrid search.")]
    out = MetadataEnricher().transform(chunks)
    assert "retrieval" in out[0].metadata["tags"]


def test_metadata_enricher_filters_stop_words_from_tags() -> None:
    chunks = [_chunk("The and for with this that")]
    out = MetadataEnricher().transform(chunks)
    assert out[0].metadata["tags"] == []


def test_metadata_enricher_preserves_existing_keys() -> None:
    chunks = [_chunk("# Title\n\nbody")]
    out = MetadataEnricher().transform(chunks)
    assert out[0].metadata["source_path"] == "x.txt"


def test_image_captioner_passes_through_when_no_vision_llm() -> None:
    chunks = [_chunk("text", cid="c1")]
    assert ImageCaptioner().transform(chunks) == chunks


def test_image_captioner_passes_through_chunks_without_images() -> None:
    capturer = ImageCaptioner(vision_llm=FakeVision(["unused"]))
    chunks = [_chunk("text")]
    assert capturer.transform(chunks) == chunks


def test_image_captioner_appends_captions_when_present() -> None:
    capturer = ImageCaptioner(vision_llm=FakeVision(["a chart"]))
    chunks = [
        _chunk(
            "see [IMAGE: img-1] in this paragraph",
        )
    ]
    chunks[0].metadata["images"] = [{"id": "img-1", "path": "/tmp/i.png"}]
    out = capturer.transform(chunks)
    assert "[img-1] a chart" in out[0].text
    assert out[0].metadata["image_captions"][0]["caption"] == "a chart"


def test_image_captioner_falls_back_silently_on_llm_error() -> None:
    class BoomVision(BaseVisionLLM):
        def chat_with_image(self, text, image, messages=None, trace=None, **kwargs):
            raise RuntimeError("boom")

    chunks = [_chunk("text")]
    chunks[0].metadata["images"] = [{"id": "img-1", "path": "/tmp/i.png"}]
    out = ImageCaptioner(vision_llm=BoomVision()).transform(chunks)
    assert out[0].text == "text"  # unchanged


class FakeVision(BaseVisionLLM):
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[ImageInput] = []

    def chat_with_image(self, text, image, messages=None, trace=None, **kwargs):
        self.calls.append(image)
        reply = self.replies.pop(0) if self.replies else ""
        return ChatResponse(content=reply, model="fake-vision")