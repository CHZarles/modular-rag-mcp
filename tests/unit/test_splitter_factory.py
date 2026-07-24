"""Tests for B3: splitter factory routing."""

from __future__ import annotations

import pytest

from src.libs.splitter.splitter_factory import (
    SplitterFactory,
    SplitterFactoryError,
)
from src.ports.ingestion import BaseSplitter


class FakeSplitter(BaseSplitter):
    """Minimal in-test splitter; configurable character length per chunk."""

    instances: list["FakeSplitter"] = []

    def __init__(self, chunk_size: int = 100) -> None:
        self.chunk_size = chunk_size
        self.calls: list[str] = []
        FakeSplitter.instances.append(self)

    def split_text(self, text: str, trace=None):  # type: ignore[override]
        self.calls.append(text)
        return [text[i : i + self.chunk_size] for i in range(0, len(text), self.chunk_size)]


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(SplitterFactory._registry)
    SplitterFactory.reset()
    yield
    SplitterFactory._registry.clear()
    SplitterFactory._registry.update(snapshot)


def test_register_and_create_routes_by_strategy() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter())
    splitter = SplitterFactory.create("fake")
    assert isinstance(splitter, FakeSplitter)


def test_factory_returns_base_splitter_instance() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter())
    splitter = SplitterFactory.create("fake")
    assert isinstance(splitter, BaseSplitter)


def test_factory_accepts_canonical_strategy_names() -> None:
    SplitterFactory.reset()
    for name in ("recursive", "semantic", "fixed_length"):
        SplitterFactory.register(name, lambda: FakeSplitter())
    assert set(SplitterFactory.registered_strategies()) == {
        "recursive",
        "semantic",
        "fixed_length",
    }


def test_create_unknown_strategy_raises_with_registered_hint() -> None:
    with pytest.raises(SplitterFactoryError) as ei:
        SplitterFactory.create("nonexistent")
    msg = str(ei.value)
    assert "nonexistent" in msg
    assert "registered=" in msg


def test_register_rejects_empty_strategy_name() -> None:
    SplitterFactory.reset()
    with pytest.raises(SplitterFactoryError, match="non-empty"):
        SplitterFactory.register("", lambda: FakeSplitter())


def test_register_rejects_non_callable_builder() -> None:
    SplitterFactory.reset()
    with pytest.raises(SplitterFactoryError, match="callable"):
        SplitterFactory.register("oops", 42)  # type: ignore[arg-type]


def test_register_overwrites_existing_strategy() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter(chunk_size=10))
    SplitterFactory.register("fake", lambda: FakeSplitter(chunk_size=50))
    splitter = SplitterFactory.create("fake")
    assert splitter.chunk_size == 50


def test_split_text_returns_list_of_strings() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter(chunk_size=5))
    splitter = SplitterFactory.create("fake")
    chunks = splitter.split_text("hello world foo bar")
    assert chunks == ["hello", " worl", "d foo", " bar"]
    assert all(isinstance(c, str) for c in chunks)


def test_split_text_with_empty_input_returns_empty_list() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter())
    splitter = SplitterFactory.create("fake")
    assert splitter.split_text("") == []


def test_reset_clears_registry() -> None:
    SplitterFactory.register("fake", lambda: FakeSplitter())
    SplitterFactory.reset()
    assert SplitterFactory.registered_strategies() == []
    with pytest.raises(SplitterFactoryError):
        SplitterFactory.create("fake")