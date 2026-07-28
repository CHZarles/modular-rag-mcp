"""按配置创建重排序器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.core.query_engine.reranker import NoneReranker
from src.ports.query import BaseReranker

RerankerBackendFactory = Callable[[Mapping[str, Any]], BaseReranker]


class RerankerFactory:
    """轻量 backend 注册表；none 是内置回退实现。"""

    _backends: dict[str, RerankerBackendFactory] = {}

    @classmethod
    def register(cls, backend: str, factory: RerankerBackendFactory) -> None:
        """注册一个 backend 名称到构造函数的映射。"""
        key = backend.strip().lower()
        if not key:
            raise ValueError("Reranker backend name must not be empty")
        cls._backends[key] = factory

    @classmethod
    def unregister(cls, backend: str) -> None:
        """测试或插件卸载时移除 backend。"""
        cls._backends.pop(backend.strip().lower(), None)

    @classmethod
    def create(cls, settings: Any) -> BaseReranker:
        """从 Settings 或 rerank 配置字典创建对应的重排序器。"""
        config = _rerank_config(settings)
        backend = str(config.get("backend", "")).strip().lower()
        if not backend:
            raise ValueError("Missing required setting: rerank.backend")

        factory = cls._backends.get(backend)
        if factory is None:
            available = ", ".join(sorted(cls._backends)) or "none"
            raise ValueError(
                f"Unsupported Reranker backend: {backend}. Registered backends: {available}"
            )
        return factory(config)


def create_reranker(settings: Any) -> BaseReranker:
    """函数式入口，便于上层装配代码少写一个类名。"""
    return RerankerFactory.create(settings)


def _rerank_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("rerank", settings)
    else:
        config = getattr(settings, "rerank", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: rerank.backend")
    return config


RerankerFactory.register("none", lambda config: NoneReranker())

__all__ = ["RerankerBackendFactory", "RerankerFactory", "create_reranker"]
