"""Embedding factory: route ``settings.embedding.provider`` to a concrete
``BaseEmbedding``.

Stage B2 scope: registry-based factory. Concrete adapters (B7.3 OpenAI/Azure,
B7.4 Ollama) are registered later.
"""

from __future__ import annotations

from typing import Callable

from src.core.settings import EmbeddingConfig
from src.ports.ingestion import BaseEmbedding

ProviderBuilder = Callable[[EmbeddingConfig], BaseEmbedding]


class EmbeddingFactoryError(ValueError):
    """Raised when an embedding provider is requested but not registered."""


class EmbeddingFactory:
    """Registry-based factory for ``BaseEmbedding`` providers."""

    _registry: dict[str, ProviderBuilder] = {}

    @classmethod
    def register(cls, provider: str, builder: ProviderBuilder) -> None:
        if not provider or not isinstance(provider, str):
            raise EmbeddingFactoryError(
                f"provider name must be a non-empty string, got {provider!r}"
            )
        if not callable(builder):
            raise EmbeddingFactoryError(f"builder for {provider!r} must be callable")
        cls._registry[provider] = builder

    @classmethod
    def unregister(cls, provider: str) -> None:
        cls._registry.pop(provider, None)

    @classmethod
    def registered_providers(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, config: EmbeddingConfig) -> BaseEmbedding:
        builder = cls._registry.get(config.provider)
        if builder is None:
            raise EmbeddingFactoryError(
                f"unknown embedding provider {config.provider!r}; "
                f"registered={cls.registered_providers() or 'none'}"
            )
        return builder(config)

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()