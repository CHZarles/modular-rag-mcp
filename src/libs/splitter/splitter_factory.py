"""Splitter factory: route a strategy name to a concrete ``BaseSplitter``.

Stage B3 scope: registry-based factory. Concrete adapters (B7.5 Recursive
Character Text Splitter, Semantic, Fixed-Length) are registered later.

The factory key is a strategy name (``recursive``, ``semantic``,
``fixed_length``) rather than the ``BaseLLM.provider``-style ``provider``
key, because splitter "providers" in DEV_SPEC §5.5 are strategies, not
vendors.
"""

from __future__ import annotations

from typing import Callable

from src.ports.ingestion import BaseSplitter

StrategyBuilder = Callable[[], BaseSplitter]


class SplitterFactoryError(ValueError):
    """Raised when a splitter strategy is requested but not registered."""


class SplitterFactory:
    """Registry-based factory for ``BaseSplitter`` strategies."""

    _registry: dict[str, StrategyBuilder] = {}

    @classmethod
    def register(cls, strategy: str, builder: StrategyBuilder) -> None:
        if not strategy or not isinstance(strategy, str):
            raise SplitterFactoryError(
                f"strategy must be a non-empty string, got {strategy!r}"
            )
        if not callable(builder):
            raise SplitterFactoryError(f"builder for {strategy!r} must be callable")
        cls._registry[strategy] = builder

    @classmethod
    def unregister(cls, strategy: str) -> None:
        cls._registry.pop(strategy, None)

    @classmethod
    def registered_strategies(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, strategy: str) -> BaseSplitter:
        builder = cls._registry.get(strategy)
        if builder is None:
            raise SplitterFactoryError(
                f"unknown splitter strategy {strategy!r}; "
                f"registered={cls.registered_strategies() or 'none'}"
            )
        return builder()

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()