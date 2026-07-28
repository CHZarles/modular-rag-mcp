from __future__ import annotations

from collections.abc import Iterator

import pytest

from libs.splitter import SplitterFactory, create_splitter


class FakeSplitter:
    def __init__(self, separator: str) -> None:
        self.separator = separator

    def split_text(self, text: str, trace: object | None = None) -> list[str]:
        return [part for part in text.split(self.separator) if part]


@pytest.fixture(autouse=True)
def cleanup_fake_providers() -> Iterator[None]:
    for provider in ("fixed", "recursive", "semantic"):
        SplitterFactory.unregister(provider)
    yield
    for provider in ("fixed", "recursive", "semantic"):
        SplitterFactory.unregister(provider)


def test_splitter_factory_routes_by_provider() -> None:
    SplitterFactory.register("fixed", lambda config: FakeSplitter(str(config["separator"])))

    splitter = SplitterFactory.create({"splitter": {"provider": "fixed", "separator": "|"}})

    assert splitter.split_text("a|b||c") == ["a", "b", "c"]


def test_splitter_factory_can_register_multiple_strategies() -> None:
    SplitterFactory.register("recursive", lambda config: FakeSplitter("\n\n"))
    SplitterFactory.register("semantic", lambda config: FakeSplitter(". "))

    recursive = create_splitter({"provider": "recursive"})
    semantic = create_splitter({"provider": "semantic"})

    assert recursive.split_text("intro\n\nbody") == ["intro", "body"]
    assert semantic.split_text("first. second") == ["first", "second"]


def test_splitter_factory_names_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported Splitter provider: missing"):
        SplitterFactory.create({"splitter": {"provider": "missing"}})


def test_splitter_factory_requires_provider() -> None:
    with pytest.raises(ValueError, match=r"splitter\.provider"):
        SplitterFactory.create({"splitter": {}})
