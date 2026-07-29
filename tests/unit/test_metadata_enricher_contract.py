from __future__ import annotations

from typing import Any

import pytest

from core.types import Chunk
from ingestion.transform import MetadataEnricher
from libs.llm import ChatResponse, Message
from ports.ingestion import BaseTransform


class FakeLLM:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[tuple[list[Message], Any | None]] = []

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append((messages, trace))
        if self.error is not None:
            raise self.error
        return ChatResponse(content=self.content, model="fake-enricher")


def make_chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        id=f"doc_{index}",
        text=text,
        metadata={"source_path": "rag-guide.pdf", "collection": "docs"},
        source_ref="doc",
        chunk_index=index,
        start_offset=10,
        end_offset=10 + len(text),
    )


def settings(use_llm: bool) -> dict[str, object]:
    return {"ingestion": {"metadata_enricher": {"use_llm": use_llm}}}


def test_rule_mode_adds_required_metadata_without_mutating_chunk() -> None:
    chunk = make_chunk(
        "# Hybrid Retrieval\n\nHybrid retrieval combines dense search with BM25 keyword matching."
    )

    result = MetadataEnricher(settings(False)).transform([chunk])[0]

    assert result is not chunk
    assert result.text == chunk.text
    assert result.id == chunk.id
    assert result.start_offset == chunk.start_offset
    assert result.metadata["source_path"] == "rag-guide.pdf"
    assert result.metadata["title"] == "Hybrid Retrieval"
    assert result.metadata["summary"]
    assert isinstance(result.metadata["tags"], list)
    assert result.metadata["tags"]
    assert result.metadata["metadata_enriched_by"] == "rule"
    assert "summary" not in chunk.metadata


def test_rule_mode_has_nonempty_fallbacks_for_empty_chunk() -> None:
    result = MetadataEnricher(settings(False)).transform([make_chunk("")])[0]

    assert result.metadata["title"] == "rag-guide"
    assert result.metadata["summary"] == "rag-guide"
    assert result.metadata["tags"] == ["rag-guide"]


def test_llm_mode_uses_structured_response_and_passes_trace() -> None:
    llm = FakeLLM(
        '<think>internal reasoning</think>\n```json\n'
        '{"title":"Hybrid Search","summary":"Dense and BM25 retrieval are fused.",'
        '"tags":["hybrid retrieval","BM25"]}\n```'
    )
    trace = object()
    chunk = make_chunk("Dense retrieval and BM25 are combined with reciprocal rank fusion.")

    result = MetadataEnricher(settings(True), llm=llm).transform([chunk], trace=trace)[0]

    assert result.metadata["title"] == "Hybrid Search"
    assert result.metadata["summary"] == "Dense and BM25 retrieval are fused."
    assert result.metadata["tags"] == ["hybrid retrieval", "BM25"]
    assert result.metadata["metadata_enriched_by"] == "llm"
    assert len(llm.calls) == 1
    assert chunk.text in llm.calls[0][0][0].content
    assert llm.calls[0][1] is trace


def test_disabled_llm_uses_rules_without_calling_model() -> None:
    llm = FakeLLM("ignored")

    result = MetadataEnricher(settings(False), llm=llm).transform([make_chunk("Useful text")])[0]

    assert result.metadata["metadata_enriched_by"] == "rule"
    assert llm.calls == []


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("", None),
        ("not json", None),
        ('{"title":"Only title"}', None),
        ("ignored", RuntimeError("model unavailable")),
    ],
    ids=["empty", "malformed-json", "missing-fields", "provider-error"],
)
def test_llm_failure_falls_back_to_complete_rule_metadata(
    content: str,
    error: Exception | None,
) -> None:
    llm = FakeLLM(content=content, error=error)

    result = MetadataEnricher(settings(True), llm=llm).transform(
        [make_chunk("# Stable Title\n\nUseful content.")]
    )[0]

    assert result.metadata["title"] == "Stable Title"
    assert result.metadata["summary"]
    assert result.metadata["tags"]
    assert result.metadata["metadata_enriched_by"] == "rule"
    assert result.metadata["metadata_enrichment_fallback_reason"] == "llm_enrichment_failed"


def test_missing_llm_configuration_also_falls_back_to_rules() -> None:
    result = MetadataEnricher(settings(True)).transform([make_chunk("Useful content")])[0]

    assert result.metadata["metadata_enriched_by"] == "rule"
    assert result.metadata["metadata_enrichment_fallback_reason"] == "llm_enrichment_failed"


def test_transform_is_idempotent_after_enrichment() -> None:
    llm = FakeLLM('{"title":"Title","summary":"Summary","tags":["tag"]}')
    enricher = MetadataEnricher(settings(True), llm=llm)

    first = enricher.transform([make_chunk("Content")])
    second = enricher.transform(first)

    assert second == first
    assert len(llm.calls) == 1


def test_metadata_enricher_matches_transform_protocol() -> None:
    assert isinstance(MetadataEnricher(settings(False)), BaseTransform)
