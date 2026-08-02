from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.types import Citation, EvaluationCase, QueryResponse
from libs.evaluator import EvaluatorFactory
from observability.evaluation.ragas_evaluator import RagasEvaluator


def test_ragas_evaluator_builds_sample_and_returns_supported_metrics() -> None:
    captured: dict[str, Any] = {}

    def runner(sample: dict[str, Any], metrics: tuple[str, ...]) -> Mapping[str, Any]:
        captured.update(sample)
        captured["metrics"] = metrics
        return {
            "faithfulness": 0.9,
            "answer_relevancy": 0.8,
            "context_precision": 0.7,
        }

    evaluator = RagasEvaluator(runner=runner)
    case = EvaluationCase(
        case_id="q1",
        query="What is RAG?",
        expected_answer="Retrieval augmented generation.",
    )

    metrics = evaluator.evaluate(case, _response())

    assert metrics == {
        "faithfulness": 0.9,
        "answer_relevancy": 0.8,
        "context_precision": 0.7,
    }
    assert captured == {
        "question": "What is RAG?",
        "answer": "It combines retrieval and generation.",
        "contexts": ["RAG retrieves context before generation."],
        "ground_truth": "Retrieval augmented generation.",
        "metrics": ("faithfulness", "answer_relevancy", "context_precision"),
    }


def test_context_precision_is_omitted_without_reference_answer() -> None:
    seen_metrics: tuple[str, ...] = ()

    def runner(sample: dict[str, Any], metrics: tuple[str, ...]) -> Mapping[str, Any]:
        nonlocal seen_metrics
        seen_metrics = metrics
        return {"faithfulness": 0.5, "answer_relevancy": 0.6}

    case = EvaluationCase(case_id="q1", query="What is RAG?")

    assert RagasEvaluator(runner=runner).evaluate(case, _response()) == {
        "faithfulness": 0.5,
        "answer_relevancy": 0.6,
    }
    assert seen_metrics == ("faithfulness", "answer_relevancy")


def test_ragas_evaluator_drops_nan_or_out_of_range_scores() -> None:
    evaluator = RagasEvaluator(
        runner=lambda sample, metrics: {
            "faithfulness": float("nan"),
            "answer_relevancy": 1.5,
            "context_precision": 0.4,
        }
    )
    case = EvaluationCase(case_id="q1", query="q", expected_answer="a")

    assert evaluator.evaluate(case, _response()) == {"context_precision": 0.4}


def test_missing_optional_dependencies_raise_actionable_import_error() -> None:
    with pytest.raises(ImportError, match="optional packages 'ragas' and 'datasets'"):
        RagasEvaluator()


def test_factory_registers_ragas_backend() -> None:
    with pytest.raises(ImportError, match="RagasEvaluator requires"):
        EvaluatorFactory.create({"evaluation": {"backends": ["ragas"]}})


def _response() -> QueryResponse:
    return QueryResponse(
        answer="It combines retrieval and generation.",
        citations=[
            Citation(
                citation_id="c1",
                chunk_id="chunk-1",
                source_path="guide.pdf",
                page=1,
                text="RAG retrieves context before generation.",
                score=0.9,
            )
        ],
        items=[],
    )
