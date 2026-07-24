"""Reranker factory: route ``settings.rerank.backend`` to a concrete ``BaseReranker``.

Stage B5 scope: registry-based factory. The default ``none`` strategy is
registered here (no-op passthrough). ``cross_encoder`` (B7.8) and
``llm`` (B7.7) adapters are registered later.
"""

from __future__ import annotations

from typing import Callable

from src.core.query_engine.reranker import NoneReranker
from src.core.settings import RerankConfig
from src.ports.query import BaseReranker

BackendBuilder = Callable[[RerankConfig], BaseReranker]


class RerankerFactoryError(ValueError):
    """Raised when a rerank backend is requested but not registered."""


class RerankerFactory:
    """Registry-based factory for ``BaseReranker`` backends."""

    _registry: dict[str, BackendBuilder] = {}

    @classmethod
    def register(cls, backend: str, builder: BackendBuilder) -> None:
        if not backend or not isinstance(backend, str):
            raise RerankerFactoryError(
                f"backend must be a non-empty string, got {backend!r}"
            )
        if not callable(builder):
            raise RerankerFactoryError(f"builder for {backend!r} must be callable")
        cls._registry[backend] = builder

    @classmethod
    def unregister(cls, backend: str) -> None:
        cls._registry.pop(backend, None)

    @classmethod
    def registered_backends(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, config: RerankConfig) -> BaseReranker:
        builder = cls._registry.get(config.backend)
        if builder is None:
            raise RerankerFactoryError(
                f"unknown rerank backend {config.backend!r}; "
                f"registered={cls.registered_backends() or 'none'}"
            )
        return builder(config)

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()


def register_default_rerankers() -> None:
    """Register the always-available ``none`` backend.

    Idempotent — safe to call from app startup multiple times.
    """
    RerankerFactory.register("none", lambda cfg: NoneReranker())