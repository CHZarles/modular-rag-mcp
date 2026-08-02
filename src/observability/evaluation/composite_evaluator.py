"""Parallel composition of independent evaluation backends."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.core.types import EvaluationCase, QueryResponse
from src.ports.evaluation import BaseEvaluator


class CompositeEvaluator:
    """Evaluate one response with multiple backends and merge their metrics."""

    name = "composite"

    def __init__(self, evaluators: list[BaseEvaluator]) -> None:
        if not evaluators:
            raise ValueError("CompositeEvaluator requires at least one evaluator")
        names = [evaluator.name for evaluator in evaluators]
        if len(names) != len(set(names)):
            raise ValueError("CompositeEvaluator evaluator names must be unique")
        self.evaluators = list(evaluators)

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        with ThreadPoolExecutor(
            max_workers=len(self.evaluators),
            thread_name_prefix="evaluator",
        ) as executor:
            futures = [
                executor.submit(evaluator.evaluate, case, response, trace)
                for evaluator in self.evaluators
            ]
            results: list[tuple[str, dict[str, float]]] = []
            for evaluator, future in zip(self.evaluators, futures, strict=True):
                try:
                    metrics = future.result()
                except Exception as exc:
                    raise RuntimeError(f"Evaluator {evaluator.name!r} failed: {exc}") from exc
                results.append((evaluator.name, metrics))

        merged: dict[str, float] = {}
        owners: dict[str, str] = {}
        for evaluator_name, metrics in results:
            for metric_name, value in metrics.items():
                if metric_name in merged:
                    raise ValueError(
                        f"Duplicate metric {metric_name!r} from evaluators "
                        f"{owners[metric_name]!r} and {evaluator_name!r}"
                    )
                merged[metric_name] = float(value)
                owners[metric_name] = evaluator_name
        return merged


__all__ = ["CompositeEvaluator"]
