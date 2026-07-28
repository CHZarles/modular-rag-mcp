from __future__ import annotations

import pytest

from libs.splitter import RecursiveSplitter, SplitterFactory


def test_factory_creates_recursive_splitter() -> None:
    splitter = SplitterFactory.create(
        {"splitter": {"provider": "recursive", "chunk_size": 80, "chunk_overlap": 10}}
    )

    assert isinstance(splitter, RecursiveSplitter)


def test_split_text_respects_markdown_blocks() -> None:
    text = """# Intro

Short introduction.

## Example

```python
def answer():
    return 42
```

## End

Final paragraph.
"""
    splitter = RecursiveSplitter({"chunk_size": 70, "chunk_overlap": 10})

    chunks = splitter.split_text(text)

    assert len(chunks) > 1
    assert any("```python\ndef answer():\n    return 42\n```" in chunk for chunk in chunks)
    assert all(heading in "\n".join(chunks) for heading in ("# Intro", "## Example", "## End"))


def test_split_text_returns_no_chunks_for_blank_input() -> None:
    assert RecursiveSplitter({}).split_text(" \n ") == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"chunk_size": 0}, "chunk_size must be positive"),
        ({"chunk_size": 10, "chunk_overlap": 10}, "smaller than chunk_size"),
    ],
)
def test_recursive_splitter_rejects_invalid_sizes(
    config: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RecursiveSplitter(config)
