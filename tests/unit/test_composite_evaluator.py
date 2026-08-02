from __future__ import annotations

from threading import Barrier
from typing import Any

import pytest

from core.types import EvaluationCase, QueryResponse
from libs.evaluator import CompositeEvaluator, EvaluatorFactory


class FixedEvaluator:
    def __init__(
        self,
        name: str,
        metrics: dict[str, float],
        barrier: Barrier | None = None,
    ) -> None:
        self.name = name
        self.metrics = metrics
        self.barrier = barrier

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        return dict(self.metrics)


def test_composite_runs_backends_concurrently_and_merges_metrics() -> None:
    barrier = Barrier(2)
    evaluator = CompositeEvaluator(
        [
            FixedEvaluator("retrieval", {"hit_rate": 1.0}, barrier),
            FixedEvaluator("judge", {"faithfulness": 0.8}, barrier),
        ]
    )

    metrics = evaluator.evaluate(_case(), _response())

    assert metrics == {"hit_rate": 1.0, "faithfulness": 0.8}


def test_composite_rejects_ambiguous_metric_ownership() -> None:
    evaluator = CompositeEvaluator(
        [
            FixedEvaluator("first", {"score": 0.5}),
            FixedEvaluator("second", {"score": 0.7}),
        ]
    )

    with pytest.raises(ValueError, match="Duplicate metric 'score'.*first.*second"):
        evaluator.evaluate(_case(), _response())


def test_composite_names_failing_backend() -> None:
    class BrokenEvaluator(FixedEvaluator):
        def evaluate(
            self,
            case: EvaluationCase,
            response: QueryResponse,
            trace: Any | None = None,
        ) -> dict[str, float]:
            raise OSError("provider down")

    evaluator = CompositeEvaluator([BrokenEvaluator("judge", {})])

    with pytest.raises(RuntimeError, match="Evaluator 'judge' failed: provider down"):
        evaluator.evaluate(_case(), _response())


def test_factory_automatically_composes_configured_backends() -> None:
    EvaluatorFactory.register("fixed", lambda config: FixedEvaluator("fixed", {"score": 0.4}))
    try:
        evaluator = EvaluatorFactory.create({"evaluation": {"backends": ["custom", "fixed"]}})
    finally:
        EvaluatorFactory.unregister("fixed")

    assert isinstance(evaluator, CompositeEvaluator)
    assert [item.name for item in evaluator.evaluators] == ["custom", "fixed"]


@pytest.mark.parametrize(
    "evaluators", [[], [FixedEvaluator("same", {}), FixedEvaluator("same", {})]]
)
def test_composite_requires_nonempty_unique_backends(evaluators: list[FixedEvaluator]) -> None:
    with pytest.raises(ValueError):
        CompositeEvaluator(evaluators)


def _case() -> EvaluationCase:
    return EvaluationCase(case_id="q1", query="question")


def _response() -> QueryResponse:
    return QueryResponse(answer="answer", citations=[], items=[])
