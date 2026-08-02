"""Optional Ragas-backed answer and context quality evaluator."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from src.core.types import EvaluationCase, QueryResponse

RagasRunner = Callable[[dict[str, Any], tuple[str, ...]], Mapping[str, Any]]
_DEFAULT_METRICS = ("faithfulness", "answer_relevancy", "context_precision")


class RagasEvaluator:
    """Adapt the optional Ragas package to the project's evaluator contract."""

    name = "ragas"

    def __init__(
        self,
        *,
        runner: RagasRunner | None = None,
        metrics: tuple[str, ...] = _DEFAULT_METRICS,
    ) -> None:
        unknown = set(metrics) - set(_DEFAULT_METRICS)
        if not metrics or unknown:
            raise ValueError(f"Unsupported Ragas metrics: {sorted(unknown)}")
        self.metrics = metrics
        self._runner = runner or _load_ragas_runner()

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        del trace
        contexts = [citation.text for citation in response.citations if citation.text.strip()]
        if not contexts:
            contexts = [item.text for item in response.items if item.text.strip()]
        enabled_metrics = tuple(
            metric
            for metric in self.metrics
            if metric != "context_precision" or case.expected_answer is not None
        )
        sample = {
            "question": case.query,
            "answer": response.answer,
            "contexts": contexts,
            "ground_truth": case.expected_answer,
        }
        raw_metrics = self._runner(sample, enabled_metrics)
        return {
            metric: score
            for metric in enabled_metrics
            if (score := _valid_score(raw_metrics.get(metric))) is not None
        }


def _load_ragas_runner() -> RagasRunner:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except ImportError as exc:
        raise ImportError(
            "RagasEvaluator requires optional packages 'ragas' and 'datasets'. "
            "Install the evaluation dependencies before selecting backend=ragas."
        ) from exc

    available = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
    }

    def run(sample: dict[str, Any], metric_names: tuple[str, ...]) -> Mapping[str, Any]:
        dataset = Dataset.from_dict(
            {
                "question": [sample["question"]],
                "answer": [sample["answer"]],
                "contexts": [sample["contexts"]],
                "ground_truth": [sample["ground_truth"]],
            }
        )
        result = evaluate(dataset, metrics=[available[name] for name in metric_names])
        return _result_mapping(result)

    return run


def _result_mapping(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return result
    scores = getattr(result, "scores", None)
    if isinstance(scores, list) and scores and isinstance(scores[0], Mapping):
        return scores[0]
    raise RuntimeError("Ragas returned an unsupported result shape")


def _valid_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    score = float(value)
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


__all__ = ["RagasEvaluator", "RagasRunner"]
