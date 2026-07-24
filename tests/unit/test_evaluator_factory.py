"""Tests for B6: evaluator factory routing + None fallback."""

from __future__ import annotations

import pytest

from src.libs.evaluator.evaluator_factory import (
    EvaluatorFactory,
    EvaluatorFactoryError,
    register_default_evaluators,
)
from src.ports.evaluation import BaseEvaluator, NoneEvaluator


class FakeCustomEvaluator(BaseEvaluator):
    """Minimal in-test evaluator that emits a single fake metric."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, case, response, trace=None):  # type: ignore[override]
        self.calls += 1
        return {"fake_score": 1.0}


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(EvaluatorFactory._registry)
    EvaluatorFactory.reset()
    yield
    EvaluatorFactory._registry.clear()
    EvaluatorFactory._registry.update(snapshot)


def test_none_backend_returns_none_evaluator() -> None:
    register_default_evaluators()
    evaluator = EvaluatorFactory.create("none")
    assert isinstance(evaluator, NoneEvaluator)
    assert isinstance(evaluator, BaseEvaluator)
    assert evaluator.name == "none"


def test_none_evaluator_returns_empty_metrics() -> None:
    register_default_evaluators()
    evaluator = EvaluatorFactory.create("none")
    assert evaluator.evaluate(case=None, response=None) == {}  # type: ignore[arg-type]


def test_custom_backend_via_registration() -> None:
    EvaluatorFactory.register("custom", lambda: FakeCustomEvaluator())
    evaluator = EvaluatorFactory.create("custom")
    assert isinstance(evaluator, FakeCustomEvaluator)


def test_register_default_evaluators_is_idempotent() -> None:
    register_default_evaluators()
    register_default_evaluators()
    assert "none" in EvaluatorFactory.registered_backends()


def test_create_unknown_backend_raises_with_registered_hint() -> None:
    EvaluatorFactory.reset()
    with pytest.raises(EvaluatorFactoryError) as ei:
        EvaluatorFactory.create("ragas")
    msg = str(ei.value)
    assert "ragas" in msg
    assert "registered=" in msg


def test_register_rejects_empty_backend_name() -> None:
    EvaluatorFactory.reset()
    with pytest.raises(EvaluatorFactoryError, match="non-empty"):
        EvaluatorFactory.register("", lambda: NoneEvaluator())


def test_register_rejects_non_callable_builder() -> None:
    EvaluatorFactory.reset()
    with pytest.raises(EvaluatorFactoryError, match="callable"):
        EvaluatorFactory.register("oops", 42)  # type: ignore[arg-type]


def test_register_overwrites_existing_backend() -> None:
    EvaluatorFactory.register("custom", lambda: FakeCustomEvaluator())
    EvaluatorFactory.register("custom", lambda: FakeCustomEvaluator())
    assert "custom" in EvaluatorFactory.registered_backends()


def test_reset_clears_registry() -> None:
    register_default_evaluators()
    EvaluatorFactory.reset()
    assert EvaluatorFactory.registered_backends() == []
    with pytest.raises(EvaluatorFactoryError):
        EvaluatorFactory.create("none")