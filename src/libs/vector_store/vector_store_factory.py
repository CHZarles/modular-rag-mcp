"""按配置创建向量存储。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.ports.ingestion import BaseVectorStore

VectorStoreBackendFactory = Callable[[Mapping[str, Any]], BaseVectorStore]


class VectorStoreFactory:
    """轻量 backend 注册表；内置存储按需注册。"""

    _backends: dict[str, VectorStoreBackendFactory] = {}

    @classmethod
    def register(cls, backend: str, factory: VectorStoreBackendFactory) -> None:
        """注册一个 backend 名称到构造函数的映射。"""
        key = backend.strip().lower()
        if not key:
            raise ValueError("VectorStore backend name must not be empty")
        cls._backends[key] = factory

    @classmethod
    def unregister(cls, backend: str) -> None:
        """测试或插件卸载时移除 backend。"""
        cls._backends.pop(backend.strip().lower(), None)

    @classmethod
    def create(cls, settings: Any) -> BaseVectorStore:
        """从 Settings 或 vector_store 配置字典创建对应的向量存储。"""
        _register_default_backends()
        config = _vector_store_config(settings)
        backend = str(config.get("backend", "")).strip().lower()
        if not backend:
            raise ValueError("Missing required setting: vector_store.backend")

        factory = cls._backends.get(backend)
        if factory is None:
            available = ", ".join(sorted(cls._backends)) or "none"
            raise ValueError(
                f"Unsupported VectorStore backend: {backend}. Registered backends: {available}"
            )
        return factory(config)


def create_vector_store(settings: Any) -> BaseVectorStore:
    """函数式入口，便于上层装配代码少写一个类名。"""
    return VectorStoreFactory.create(settings)


def _vector_store_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("vector_store", settings)
    else:
        config = getattr(settings, "vector_store", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: vector_store.backend")
    return config


def _register_default_backends() -> None:
    """确保内置 Chroma 后端可用，同时不覆盖调用方注册的同名实现。"""
    if "chroma" in VectorStoreFactory._backends:
        return

    from src.libs.vector_store.chroma_store import ChromaStore

    VectorStoreFactory.register("chroma", lambda config: ChromaStore(config))


__all__ = ["VectorStoreBackendFactory", "VectorStoreFactory", "create_vector_store"]
