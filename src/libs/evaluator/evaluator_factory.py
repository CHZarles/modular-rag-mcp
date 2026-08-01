"""按配置创建评估器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.libs.evaluator.custom_evaluator import CustomEvaluator
from src.ports.evaluation import BaseEvaluator

EvaluatorBackendFactory = Callable[[Mapping[str, Any]], BaseEvaluator]


class EvaluatorFactory:
    """评估后端注册表，支持创建单个或多个评估器。"""

    _backends: dict[str, EvaluatorBackendFactory] = {}

    @classmethod
    def register(cls, backend: str, factory: EvaluatorBackendFactory) -> None:
        key = backend.strip().lower()
        if not key:
            raise ValueError("Evaluator backend name must not be empty")
        cls._backends[key] = factory

    @classmethod
    def unregister(cls, backend: str) -> None:
        cls._backends.pop(backend.strip().lower(), None)

    @classmethod
    def create(cls, settings: Any, backend: str | None = None) -> BaseEvaluator:
        """创建一个后端；配置多个后端时应改用 ``create_all``。"""
        config = _evaluation_config(settings)
        selected = backend.strip().lower() if backend is not None else _single_backend(config)
        return cls._create_backend(selected, config)

    @classmethod
    def create_all(cls, settings: Any) -> list[BaseEvaluator]:
        """按 ``evaluation.backends`` 的声明顺序创建所有评估器。"""
        config = _evaluation_config(settings)
        return [cls._create_backend(backend, config) for backend in _backend_names(config)]

    @classmethod
    def _create_backend(
        cls,
        backend: str,
        config: Mapping[str, Any],
    ) -> BaseEvaluator:
        factory = cls._backends.get(backend)
        if factory is None:
            available = ", ".join(sorted(cls._backends)) or "none"
            raise ValueError(
                f"Unsupported Evaluator backend: {backend}. Registered backends: {available}"
            )
        return factory(config)


def create_evaluator(settings: Any, backend: str | None = None) -> BaseEvaluator:
    return EvaluatorFactory.create(settings, backend=backend)


def create_evaluators(settings: Any) -> list[BaseEvaluator]:
    return EvaluatorFactory.create_all(settings)


def _evaluation_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("evaluation", settings)
    else:
        config = getattr(settings, "evaluation", None)
    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: evaluation.backends")
    return config


def _backend_names(config: Mapping[str, Any]) -> list[str]:
    raw = config.get("backends")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ValueError("evaluation.backends must be a non-empty list")

    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("evaluation.backends entries must be non-empty strings")
        names.append(value.strip().lower())
    return names


def _single_backend(config: Mapping[str, Any]) -> str:
    names = _backend_names(config)
    if len(names) != 1:
        raise ValueError("Multiple evaluation.backends configured; use EvaluatorFactory.create_all")
    return names[0]


EvaluatorFactory.register("custom", lambda config: CustomEvaluator())
EvaluatorFactory.register("custom_metrics", lambda config: CustomEvaluator())

__all__ = [
    "EvaluatorBackendFactory",
    "EvaluatorFactory",
    "create_evaluator",
    "create_evaluators",
]
