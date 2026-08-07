"""Golden-set evaluation orchestration over the configured query engine."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from src.core.settings import Settings
from src.core.types import (
    EvaluationCase,
    EvaluationReport,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.ports.evaluation import BaseEvaluator
from src.ports.query import QueryEngine


class EvalRunner:
    """Load a versioned golden set, run retrieval, and aggregate evaluator metrics."""

    def __init__(
        self,
        settings: Settings,
        hybrid_search: QueryEngine,
        evaluator: BaseEvaluator,
    ) -> None:
        self.settings = settings
        self.hybrid_search = hybrid_search
        self.evaluator = evaluator

    def run(self, test_set_path: str | Path) -> EvaluationReport:
        path = Path(test_set_path).expanduser()
        cases = load_golden_test_set(path)
        rows: list[dict[str, Any]] = []
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}

        for case in cases:
            response = self._query(case)
            metrics = self.evaluator.evaluate(case, response)
            for name, value in metrics.items():
                totals[name] = totals.get(name, 0.0) + float(value)
                counts[name] = counts.get(name, 0) + 1
            rows.append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "expected_chunk_ids": list(case.expected_chunk_ids),
                    "retrieved_chunk_ids": [item.chunk_id for item in response.results],
                    "retrieved_sources": [_source(item) for item in response.results],
                    "metrics": {name: float(value) for name, value in metrics.items()},
                }
            )

        aggregate = {
            name: totals[name] / counts[name] for name in sorted(totals) if counts[name] > 0
        }
        return EvaluationReport(
            run_id=str(uuid.uuid4()),
            metrics=aggregate,
            cases=rows,
            metadata={
                "test_set_path": str(path.resolve()),
                "case_count": len(cases),
                "evaluator": self.evaluator.name,
            },
        )

    def _query(self, case: EvaluationCase) -> QueryResponse:
        top_k = _positive_int(
            case.metadata.get("top_k", self.settings.retrieval.get("top_k_final", 5)),
            "top_k",
        )
        collection = case.metadata.get("collection", "default")
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError(f"Evaluation case {case.case_id!r} has invalid collection")
        filters = case.metadata.get("filters", {})
        if not isinstance(filters, dict):
            raise ValueError(f"Evaluation case {case.case_id!r} has invalid filters")
        candidates = self.hybrid_search.search(
            QueryRequest(
                query=case.query,
                top_k=top_k,
                collection=collection.strip(),
                filters=dict(filters),
                include_images=False,
                request_id=case.case_id,
            )
        )
        return QueryResponse(
            results=candidates,
            request_id=case.case_id,
            metadata={"evaluation_case_id": case.case_id},
        )


def load_golden_test_set(path: str | Path) -> list[EvaluationCase]:
    selected = Path(path).expanduser()
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Golden test set is not valid JSON: {selected}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("test_cases"), list):
        raise ValueError("Golden test set must contain a test_cases list")
    if not payload["test_cases"]:
        raise ValueError("Golden test set must contain at least one case")

    cases = [_parse_case(item, index) for index, item in enumerate(payload["test_cases"], 1)]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Golden test set case IDs must be unique")
    return cases


def _parse_case(payload: Any, index: int) -> EvaluationCase:
    if not isinstance(payload, dict):
        raise ValueError(f"Golden test case {index} must be an object")
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"Golden test case {index} query must be non-empty")
    expected_ids = _string_list(payload.get("expected_chunk_ids", []), "expected_chunk_ids")
    expected_sources = _string_list(payload.get("expected_sources", []), "expected_sources")
    expected_answer = payload.get("expected_answer")
    if expected_answer is not None and not isinstance(expected_answer, str):
        raise ValueError(f"Golden test case {index} expected_answer must be text")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"Golden test case {index} metadata must be an object")
    if expected_sources:
        metadata = {**metadata, "expected_sources": expected_sources}
    case_id = payload.get("case_id", f"case-{index:03d}")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"Golden test case {index} case_id must be non-empty")
    return EvaluationCase(
        case_id=case_id.strip(),
        query=query.strip(),
        expected_chunk_ids=expected_ids,
        expected_answer=expected_answer,
        metadata=dict(metadata),
    )


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"Golden test case {field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Evaluation {field} must be a positive integer")
    return value


def _source(candidate: RetrievalCandidate) -> str | None:
    value = candidate.metadata.get("source_path") or candidate.metadata.get("source")
    return value if isinstance(value, str) else None


__all__ = ["EvalRunner", "load_golden_test_set"]
