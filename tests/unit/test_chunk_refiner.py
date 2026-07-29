from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.types import Chunk
from ingestion.transform import ChunkRefiner
from libs.llm import ChatResponse, Message
from ports.ingestion import BaseTransform

_FIXTURES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "noisy_chunks.json").read_text(encoding="utf-8")
)


class FakeLLM:
    def __init__(self, content: str = "LLM refined text", error: Exception | None = None) -> None:
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
        return ChatResponse(content=self.content, model="fake-refiner")


def make_chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        id=f"doc_000{index}_hash",
        text=text,
        metadata={"source_path": "document.pdf", "chunk_index": index},
        source_ref="doc",
        chunk_index=index,
        start_offset=0,
        end_offset=len(text),
    )


def settings(use_llm: bool) -> dict[str, object]:
    return {"ingestion": {"chunk_refiner": {"use_llm": use_llm}}}


@pytest.mark.parametrize(
    ("case"),
    _FIXTURES,
    ids=[str(case["name"]) for case in _FIXTURES],
)
def test_rule_refiner_cleans_fixture_without_losing_structure(case: dict[str, str]) -> None:
    refiner = ChunkRefiner(settings(False))

    assert refiner._rule_based_refine(case["input"]) == case["expected"]


def test_rule_mode_returns_new_chunk_and_preserves_domain_fields() -> None:
    chunk = make_chunk("[页眉] Header\n\nUseful   text.\n\nPage 1 of 1")

    result = ChunkRefiner(settings(False)).transform([chunk])[0]

    assert result is not chunk
    assert result.text == "Useful text."
    assert result.id == chunk.id
    assert result.source_ref == chunk.source_ref
    assert result.start_offset == chunk.start_offset
    assert result.metadata["refined_by"] == "rule"
    assert "refined_by" not in chunk.metadata


def test_rule_refiner_preserves_nested_markdown_indentation() -> None:
    text = "1. Parent\n   - Child   item\n      1. Grandchild"

    result = ChunkRefiner(settings(False))._rule_based_refine(text)

    assert result == "1. Parent\n   - Child item\n      1. Grandchild"


def test_llm_mode_sends_rule_cleaned_text_and_uses_response(tmp_path: Path) -> None:
    prompt_path = tmp_path / "refine.txt"
    prompt_path.write_text("Improve this chunk:\n{text}", encoding="utf-8")
    llm = FakeLLM("Refined by model")
    trace = object()
    refiner = ChunkRefiner(settings(True), llm=llm, prompt_path=prompt_path)

    result = refiner.transform([make_chunk("Page 1 of 2\n\nUseful   text.")], trace=trace)[0]

    assert result.text == "Refined by model"
    assert result.metadata["refined_by"] == "llm"
    assert len(llm.calls) == 1
    assert llm.calls[0][0] == [Message(role="user", content="Improve this chunk:\nUseful text.")]
    assert llm.calls[0][1] is trace


def test_disabled_llm_is_not_called() -> None:
    llm = FakeLLM()

    result = ChunkRefiner(settings(False), llm=llm).transform([make_chunk("Useful text.")])[0]

    assert result.metadata["refined_by"] == "rule"
    assert llm.calls == []


def test_llm_reasoning_block_is_not_written_to_chunk() -> None:
    llm = FakeLLM("<think>internal reasoning</think>\n\nRefined text")

    result = ChunkRefiner(settings(True), llm=llm).transform([make_chunk("Original")])[0]

    assert result.text == "Refined text"
    assert "think" not in result.text


@pytest.mark.parametrize(
    ("content", "error"),
    [("", None), ("ignored", RuntimeError("model unavailable"))],
    ids=["empty-response", "provider-error"],
)
def test_llm_failure_falls_back_to_rule_result(
    content: str,
    error: Exception | None,
) -> None:
    llm = FakeLLM(content=content, error=error)

    result = ChunkRefiner(settings(True), llm=llm).transform(
        [make_chunk("Page 1 of 2\n\nUseful   text.")]
    )[0]

    assert result.text == "Useful text."
    assert result.metadata["refined_by"] == "rule"
    assert result.metadata["refinement_fallback_reason"] == "llm_refinement_failed"


def test_transform_is_idempotent_after_first_refinement() -> None:
    llm = FakeLLM("Stable refinement")
    refiner = ChunkRefiner(settings(True), llm=llm)

    first = refiner.transform([make_chunk("Original text")])
    second = refiner.transform(first)

    assert second == first
    assert len(llm.calls) == 1


def test_single_chunk_exception_keeps_original_and_does_not_stop_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refiner = ChunkRefiner(settings(False))
    original_rule = refiner._rule_based_refine

    def fail_one(text: str) -> str:
        if text == "broken":
            raise ValueError("bad chunk")
        return original_rule(text)

    monkeypatch.setattr(refiner, "_rule_based_refine", fail_one)

    results = refiner.transform([make_chunk("broken"), make_chunk("Useful   text", 1)])

    assert results[0].text == "broken"
    assert results[0].metadata["refined_by"] == "none"
    assert "bad chunk" in results[0].metadata["refinement_error"]
    assert results[1].text == "Useful text"
    assert results[1].metadata["refined_by"] == "rule"


def test_missing_prompt_file_uses_default_template(tmp_path: Path) -> None:
    llm = FakeLLM()
    refiner = ChunkRefiner(settings(True), llm=llm, prompt_path=tmp_path / "missing.txt")

    refiner.transform([make_chunk("Content marker")])

    assert "Content marker" in llm.calls[0][0][0].content


def test_chunk_refiner_matches_transform_protocol() -> None:
    assert isinstance(ChunkRefiner(settings(False)), BaseTransform)
