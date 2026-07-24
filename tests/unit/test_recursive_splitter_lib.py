"""Tests for B7.5: RecursiveCharacterSplitter + SplitterFactory routing."""

from __future__ import annotations

import pytest

from src.libs.splitter.recursive_splitter import RecursiveCharacterSplitter
from src.libs.splitter.splitter_factory import SplitterFactory
from src.ports.ingestion import BaseSplitter


def test_recursive_splitter_splits_long_text() -> None:
    text = "Lorem ipsum dolor sit amet. " * 100  # ~2800 chars
    splitter = RecursiveCharacterSplitter(chunk_size=200, chunk_overlap=20)
    chunks = splitter.split_text(text)
    assert all(len(c) <= 220 for c in chunks)  # +overlap tolerance
    assert len(chunks) > 1


def test_recursive_splitter_short_text_single_chunk() -> None:
    splitter = RecursiveCharacterSplitter(chunk_size=200)
    assert splitter.split_text("short text") == ["short text"]


def test_recursive_splitter_empty_text_returns_empty_list() -> None:
    assert RecursiveCharacterSplitter().split_text("") == []


def test_recursive_splitter_preserves_markdown_headings() -> None:
    """Markdown headings stay attached to the next paragraph when both fit
    inside the chunk budget. The recursion only kicks in when forced."""
    md = (
        "# Title\n\n"
        "Intro paragraph one with details.\n\n"
        "## Subsection\n\n"
        "Body text under the subsection that should stay together."
    )
    splitter = RecursiveCharacterSplitter(chunk_size=120, chunk_overlap=0)
    chunks = splitter.split_text(md)
    # No chunk should consist of a bare heading token like "# Title".
    for chunk in chunks:
        stripped = chunk.strip()
        assert stripped not in {"# Title", "## Subsection"}, (
            f"heading orphaned from its body: {chunk!r}"
        )


def test_recursive_splitter_preserves_code_blocks() -> None:
    """A short code block must not be split mid-line when chunk_size is generous."""
    md = (
        "Paragraph intro.\n\n"
        "```\n"
        "def hello():\n"
        "    print('world')\n"
        "```\n\n"
        "Paragraph outro."
    )
    splitter = RecursiveCharacterSplitter(chunk_size=200, chunk_overlap=0)
    chunks = splitter.split_text(md)
    assert any("def hello()" in c and "print('world')" in c for c in chunks)


def test_recursive_splitter_invalid_params() -> None:
    with pytest.raises(ValueError, match="chunk_size must be > 0"):
        RecursiveCharacterSplitter(chunk_size=0)
    with pytest.raises(ValueError, match="chunk_overlap"):
        RecursiveCharacterSplitter(chunk_size=100, chunk_overlap=100)


def test_recursive_splitter_invalid_input_raises() -> None:
    with pytest.raises(ValueError, match="text must be a string"):
        RecursiveCharacterSplitter().split_text(123)  # type: ignore[arg-type]


def test_factory_routes_recursive_strategy() -> None:
    snapshot = dict(SplitterFactory._registry)
    SplitterFactory.reset()
    try:
        SplitterFactory.register(
            "recursive", lambda: RecursiveCharacterSplitter(chunk_size=100)
        )
        splitter = SplitterFactory.create("recursive")
        assert isinstance(splitter, RecursiveCharacterSplitter)
        assert isinstance(splitter, BaseSplitter)
        chunks = splitter.split_text("Hello world. " * 50)
        assert len(chunks) > 1
    finally:
        SplitterFactory._registry.clear()
        SplitterFactory._registry.update(snapshot)