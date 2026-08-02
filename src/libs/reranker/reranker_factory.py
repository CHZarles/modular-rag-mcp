"""按配置创建重排序器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.core.query_engine.reranker import FallbackReranker, NoneReranker
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
        _register_default_backends()
        config = _rerank_config(settings)
        if _is_disabled(config):
            return NoneReranker()
        backend = str(config.get("backend", "")).strip().lower()
        if not backend:
            raise ValueError("Missing required setting: rerank.backend")

        backend_factory = cls._backends.get(backend)
        if backend_factory is None:
            available = ", ".join(sorted(cls._backends)) or "none"
            raise ValueError(
                f"Unsupported Reranker backend: {backend}. Registered backends: {available}"
            )

        top_m = _optional_positive_int(config, "top_m")
        timeout_seconds = _optional_positive_float(config, "timeout_seconds")
        implementation = backend_factory(_backend_config(settings, config))
        # none 本身不会失败，也没有远程耗时；避免为默认空操作创建线程池包装。
        if backend == "none":
            return implementation
        return FallbackReranker(
            implementation,
            backend_name=backend,
            top_m=top_m,
            timeout_seconds=timeout_seconds,
        )


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


def _backend_config(settings: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    """给具体后端补充所需的顶层配置，同时保持原 rerank 字段不变。"""
    backend_config = dict(config)
    if "llm" in backend_config:
        return backend_config

    if isinstance(settings, Mapping):
        llm_config = settings.get("llm") if "rerank" in settings else None
    else:
        llm_config = getattr(settings, "llm", None)
    if isinstance(llm_config, Mapping):
        backend_config["llm"] = llm_config
    return backend_config


def _optional_positive_int(config: Mapping[str, Any], key: str) -> int | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"rerank.{key} must be a positive integer")
    return value


def _optional_positive_float(config: Mapping[str, Any], key: str) -> float | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"rerank.{key} must be a positive number")
    return float(value)


def _is_disabled(config: Mapping[str, Any]) -> bool:
    value = config.get("enabled", True)
    if isinstance(value, bool):
        return not value
    return str(value).strip().lower() in {"false", "0", "no", "off", "disabled"}


def _register_default_backends() -> None:
    if "cross_encoder" not in RerankerFactory._backends:
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker

        RerankerFactory.register(
            "cross_encoder", lambda config: CrossEncoderReranker(config)
        )
    if "llm" not in RerankerFactory._backends:
        from src.libs.reranker.llm_reranker import LLMReranker

        RerankerFactory.register("llm", lambda config: LLMReranker(config))


RerankerFactory.register("none", lambda config: NoneReranker())

__all__ = ["RerankerBackendFactory", "RerankerFactory", "create_reranker"]
