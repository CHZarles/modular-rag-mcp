from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.evaluate as evaluate_script
from core.settings import Settings
from core.types import QueryRequest, RetrievalCandidate
from libs.evaluator import CustomEvaluator
from observability.evaluation import EvalRunner
from observability.evaluation.eval_runner import load_golden_test_set


class FakeQueryEngine:
    def __init__(self) -> None:
        self.requests: list[QueryRequest] = []

    def search(
        self,
        request: QueryRequest,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        self.requests.append(request)
        chunk_ids = ["gold", "other"] if request.query == "first" else ["other"]
        return [
            RetrievalCandidate(
                chunk_id=chunk_id,
                text=f"text {chunk_id}",
                metadata={"source_path": f"{chunk_id}.pdf"},
                score=1.0 / rank,
                source="fusion",
                rank=rank,
            )
            for rank, chunk_id in enumerate(chunk_ids, 1)
        ]


def test_eval_runner_loads_golden_set_queries_and_aggregates_metrics(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            {
                "test_cases": [
                    {
                        "case_id": "one",
                        "query": "first",
                        "expected_chunk_ids": ["gold"],
                        "metadata": {"collection": "docs", "top_k": 2},
                    },
                    {
                        "case_id": "two",
                        "query": "second",
                        "expected_chunk_ids": ["missing"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    engine = FakeQueryEngine()
    runner = EvalRunner(_settings(), engine, CustomEvaluator())  # type: ignore[arg-type]

    report = runner.run(path)

    assert report.metrics == {"hit_rate": 0.5, "mrr": 0.5}
    assert report.metadata["case_count"] == 2
    assert report.metadata["evaluator"] == "custom"
    assert report.cases[0]["retrieved_chunk_ids"] == ["gold", "other"]
    assert [(request.collection, request.top_k) for request in engine.requests] == [
        ("docs", 2),
        ("default", 5),
    ]


def test_golden_loader_derives_ids_and_preserves_expected_sources(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            {
                "test_cases": [
                    {
                        "query": "question",
                        "expected_chunk_ids": [],
                        "expected_sources": ["guide.pdf"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_golden_test_set(path)

    assert cases[0].case_id == "case-001"
    assert cases[0].metadata["expected_sources"] == ["guide.pdf"]


def test_evaluate_cli_prints_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps({"test_cases": [{"query": "first", "expected_chunk_ids": ["gold"]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(evaluate_script, "load_settings", lambda path: _settings())
    monkeypatch.setattr(
        evaluate_script, "build_local_query_engine", lambda settings: FakeQueryEngine()
    )
    monkeypatch.setattr(evaluate_script, "create_evaluator", lambda settings: CustomEvaluator())

    exit_code = evaluate_script.main(["--test-set", str(path)], settings_path="unused.yaml")

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["metrics"] == {"hit_rate": 1.0, "mrr": 1.0}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"test_cases": []},
        {"test_cases": [{"query": ""}]},
        {"test_cases": [{"case_id": "same", "query": "a"}, {"case_id": "same", "query": "b"}]},
    ],
)
def test_golden_loader_rejects_invalid_contract(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_golden_test_set(path)


def _settings() -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25", "top_k_final": 5},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
    )
