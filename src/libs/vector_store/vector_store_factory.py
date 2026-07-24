"""VectorStore factory: route ``settings.vector_store.backend`` to a concrete
``BaseVectorStore``.

Stage B4 scope: registry-based factory. The default Chroma adapter
(B7.6) is registered later. Tests run against fakes.
"""

from __future__ import annotations

from typing import Callable

from src.core.settings import VectorStoreConfig
from src.ports.ingestion import BaseVectorStore

BackendBuilder = Callable[[VectorStoreConfig], BaseVectorStore]


class VectorStoreFactoryError(ValueError):
    """Raised when a vector-store backend is requested but not registered."""


class VectorStoreFactory:
    """Registry-based factory for ``BaseVectorStore`` backends."""

    _registry: dict[str, BackendBuilder] = {}

    @classmethod
    def register(cls, backend: str, builder: BackendBuilder) -> None:
        if not backend or not isinstance(backend, str):
            raise VectorStoreFactoryError(
                f"backend must be a non-empty string, got {backend!r}"
            )
        if not callable(builder):
            raise VectorStoreFactoryError(f"builder for {backend!r} must be callable")
        cls._registry[backend] = builder

    @classmethod
    def unregister(cls, backend: str) -> None:
        cls._registry.pop(backend, None)

    @classmethod
    def registered_backends(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, config: VectorStoreConfig) -> BaseVectorStore:
        builder = cls._registry.get(config.backend)
        if builder is None:
            raise VectorStoreFactoryError(
                f"unknown vector-store backend {config.backend!r}; "
                f"registered={cls.registered_backends() or 'none'}"
            )
        return builder(config)

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()