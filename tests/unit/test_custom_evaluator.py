from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from core.settings import Settings
from core.types import EvaluationCase, QueryResponse, RetrievalCandidate
from libs.evaluator import (
    BaseEvaluator,
    CustomEvaluator,
    EvaluatorFactory,
    create_evaluator,
    create_evaluators,
)


class FixedEvaluator:
    name = "fixed"

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        return {"score": 0.5}


@pytest.fixture(autouse=True)
def cleanup_fixed_backend() -> Iterator[None]:
    EvaluatorFactory.unregister("fixed")
    yield
    EvaluatorFactory.unregister("fixed")


def candidate(chunk_id: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text {chunk_id}",
        metadata={},
        score=1.0 / rank,
        source="fusion",
        rank=rank,
    )


def response(*chunk_ids: str) -> QueryResponse:
    return QueryResponse(
        answer="",
        citations=[],
        items=[candidate(chunk_id, rank) for rank, chunk_id in enumerate(chunk_ids, start=1)],
    )


def test_custom_evaluator_returns_hit_rate_and_reciprocal_rank() -> None:
    case = EvaluationCase(case_id="q1", query="where?", expected_chunk_ids=["golden"])

    metrics = CustomEvaluator().evaluate(case, response("other", "golden", "later"))

    assert metrics == {"hit_rate": 1.0, "mrr": 0.5}


@pytest.mark.parametrize(
    ("golden_ids", "retrieved_ids"),
    [(["missing"], ["a", "b"]), ([], ["a"]), (["a"], [])],
)
def test_custom_evaluator_returns_zero_for_no_relevant_hit(
    golden_ids: list[str],
    retrieved_ids: list[str],
) -> None:
    case = EvaluationCase(case_id="q1", query="where?", expected_chunk_ids=golden_ids)

    assert CustomEvaluator().evaluate(case, response(*retrieved_ids)) == {
        "hit_rate": 0.0,
        "mrr": 0.0,
    }


def test_custom_evaluator_computes_source_level_recall_for_stable_golden_sets() -> None:
    case = EvaluationCase(
        case_id="q1",
        query="where?",
        metadata={"expected_sources": ["guide.pdf"]},
    )
    result = response("other", "golden")
    result.items[1].metadata["source_path"] = "/documents/guide.pdf"

    assert CustomEvaluator().evaluate(case, result) == {
        "hit_rate": 0.0,
        "mrr": 0.0,
        "source_hit_rate": 1.0,
        "source_mrr": 0.5,
    }


def test_custom_evaluator_normalizes_source_basename_and_case_across_platforms() -> None:
    case = EvaluationCase(
        case_id="q1",
        query="where?",
        metadata={"expected_sources": [r"C:\golden\GUIDE.PDF"]},
    )
    result = response("first", "matching")
    result.items[0].metadata["source_path"] = "/documents/other.pdf"
    result.items[1].metadata["source"] = "/runtime/guide.pdf"

    assert CustomEvaluator().evaluate(case, result) == {
        "hit_rate": 0.0,
        "mrr": 0.0,
        "source_hit_rate": 1.0,
        "source_mrr": 0.5,
    }


@pytest.mark.parametrize(
    "expected_sources",
    [None, "guide.pdf", [], [""], [None, 1]],
)
def test_custom_evaluator_omits_source_metrics_without_valid_source_contract(
    expected_sources: object,
) -> None:
    case = EvaluationCase(
        case_id="q1",
        query="where?",
        metadata={"expected_sources": expected_sources},
    )

    assert CustomEvaluator().evaluate(case, response("missing")) == {
        "hit_rate": 0.0,
        "mrr": 0.0,
    }


def test_factory_creates_custom_evaluator_from_settings() -> None:
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
    )

    evaluator = EvaluatorFactory.create(settings)

    assert isinstance(evaluator, CustomEvaluator)
    assert isinstance(evaluator, BaseEvaluator)


def test_factory_creates_registered_backends_in_config_order() -> None:
    def build_fixed(config: Mapping[str, Any]) -> BaseEvaluator:
        return FixedEvaluator()

    EvaluatorFactory.register("fixed", build_fixed)

    evaluators = create_evaluators({"backends": ["fixed", "custom_metrics"]})

    assert [evaluator.name for evaluator in evaluators] == ["fixed", "custom"]
    assert isinstance(create_evaluator({"backends": ["custom"]}), CustomEvaluator)


def test_factory_names_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported Evaluator backend: missing"):
        EvaluatorFactory.create({"evaluation": {"backends": ["missing"]}})


@pytest.mark.parametrize("backends", [None, [], "custom", [""]])
def test_factory_requires_non_empty_backend_list(backends: object) -> None:
    with pytest.raises(ValueError, match=r"evaluation\.backends"):
        EvaluatorFactory.create({"evaluation": {"backends": backends}})


def test_factory_composes_multiple_backends() -> None:
    EvaluatorFactory.register("fixed", lambda config: FixedEvaluator())
    evaluator = EvaluatorFactory.create({"backends": ["custom", "fixed"]})

    assert evaluator.name == "composite"


def test_factory_backend_override_accepts_config_without_backend_list() -> None:
    evaluator = create_evaluator({}, backend=" CUSTOM ")

    assert isinstance(evaluator, CustomEvaluator)


def test_evaluator_factory_rejects_empty_registered_name() -> None:
    with pytest.raises(ValueError, match="backend name must not be empty"):
        EvaluatorFactory.register(" ", lambda config: FixedEvaluator())
