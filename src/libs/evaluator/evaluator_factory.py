"""Evaluator factory: route evaluator backend names to ``BaseEvaluator`` instances.

Stage B6 scope: registry-based factory. Concrete adapters (Ragas, Custom)
are registered later. The ``none`` backend is registered here so the
factory always has a safe fallback.
"""

from __future__ import annotations

from typing import Callable

from src.ports.evaluation import BaseEvaluator, NoneEvaluator

BackendBuilder = Callable[[], BaseEvaluator]


class EvaluatorFactoryError(ValueError):
    """Raised when an evaluator backend is requested but not registered."""


class EvaluatorFactory:
    """Registry-based factory for ``BaseEvaluator`` backends.

    Unlike LLM/Embedding, evaluators don't take a config object — they
    are stateless metric calculators — so builders take no arguments.
    """

    _registry: dict[str, BackendBuilder] = {}

    @classmethod
    def register(cls, backend: str, builder: BackendBuilder) -> None:
        if not backend or not isinstance(backend, str):
            raise EvaluatorFactoryError(
                f"backend must be a non-empty string, got {backend!r}"
            )
        if not callable(builder):
            raise EvaluatorFactoryError(f"builder for {backend!r} must be callable")
        cls._registry[backend] = builder

    @classmethod
    def unregister(cls, backend: str) -> None:
        cls._registry.pop(backend, None)

    @classmethod
    def registered_backends(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, backend: str) -> BaseEvaluator:
        builder = cls._registry.get(backend)
        if builder is None:
            raise EvaluatorFactoryError(
                f"unknown evaluator backend {backend!r}; "
                f"registered={cls.registered_backends() or 'none'}"
            )
        return builder()

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()


def register_default_evaluators() -> None:
    """Register the always-available ``none`` backend. Idempotent."""
    EvaluatorFactory.register("none", lambda: NoneEvaluator())