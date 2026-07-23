"""Evaluation ports."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import EvaluationCase, EvaluationReport, QueryResponse


@runtime_checkable
class BaseEvaluator(Protocol):
    name: str

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]: ...


@runtime_checkable
class EvalRunner(Protocol):
    def run(
        self,
        cases: list[EvaluationCase],
        evaluators: list[BaseEvaluator],
    ) -> EvaluationReport: ...


class NoneEvaluator:
    """No-op evaluator used when evaluation is disabled."""

    name = "none"

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        return {}
