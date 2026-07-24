"""Tests for C5: ChunkRefiner (default Transform implementation)."""

from __future__ import annotations

from src.core.types import Chunk
from src.ingestion.transform.chunk_refiner import ChunkRefiner
from src.ports.ingestion import BaseTransform


def _chunk(text: str, cid: str = "c1", idx: int = 0) -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata={"source_path": "x.txt"},
        source_ref="d1",
        chunk_index=idx,
    )


def test_chunk_refiner_is_a_base_transform() -> None:
    assert isinstance(ChunkRefiner(), BaseTransform)
    assert ChunkRefiner.name == "chunk_refiner"


def test_collapses_runs_of_spaces() -> None:
    chunks = [_chunk("hello     world")]
    out = ChunkRefiner().transform(chunks)
    assert out[0].text == "hello world"


def test_collapses_excess_blank_lines() -> None:
    chunks = [_chunk("a\n\n\n\n\n\nb")]
    out = ChunkRefiner().transform(chunks)
    assert out[0].text == "a\n\nb"


def test_strips_leading_and_trailing_whitespace() -> None:
    chunks = [_chunk("   padded   ")]
    out = ChunkRefiner().transform(chunks)
    assert out[0].text == "padded"


def test_empty_text_round_trip() -> None:
    chunks = [_chunk("")]
    out = ChunkRefiner().transform(chunks)
    assert out[0].text == ""


def test_preserves_chunk_metadata_and_id() -> None:
    chunks = [_chunk("hello   world", cid="c-X", idx=3)]
    out = ChunkRefiner().transform(chunks)
    assert out[0].id == "c-X"
    assert out[0].chunk_index == 3
    assert out[0].metadata["source_path"] == "x.txt"


def test_empty_input_returns_empty_output() -> None:
    assert ChunkRefiner().transform([]) == []


def test_multiple_chunks_all_refined() -> None:
    chunks = [_chunk("a   b", cid="c1", idx=0), _chunk("c\n\n\nd", cid="c2", idx=1)]
    out = ChunkRefiner().transform(chunks)
    assert [c.text for c in out] == ["a b", "c\n\nd"]


def test_does_not_mutate_input_chunks() -> None:
    chunks = [_chunk("hello   world")]
    ChunkRefiner().transform(chunks)
    assert chunks[0].text == "hello   world"