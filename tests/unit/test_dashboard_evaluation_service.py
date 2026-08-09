from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from core.settings import Settings
from core.types import JsonDict, QueryRequest, RetrievalCandidate
from observability.dashboard.services import (
    EvaluationDashboardService,
    HotpotQABenchmarkJobService,
)


class FakeQueryEngine:
    def search(
        self,
        request: QueryRequest,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        return [
            RetrievalCandidate(
                chunk_id="gold",
                text="answer",
                metadata={"source_path": "guide.pdf"},
                score=1.0,
                source="fusion",
                rank=1,
            )
        ]


def test_evaluation_service_lists_configuration_and_runs_selected_backend(
    tmp_path: Path,
) -> None:
    golden = tmp_path / "golden.json"
    golden.write_text(
        json.dumps({"test_cases": [{"query": "question", "expected_chunk_ids": ["gold"]}]}),
        encoding="utf-8",
    )
    service = EvaluationDashboardService(
        _settings(str(golden)),
        FakeQueryEngine(),  # type: ignore[arg-type]
    )

    report = service.run(golden, ["custom"])

    assert service.available_backends() == ["custom"]
    assert golden.resolve() in service.golden_test_sets()
    assert report.metrics == {"hit_rate": 1.0, "mrr": 1.0}


def test_evaluation_service_runs_hotpotqa_with_saved_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("evaluation:\n  backends: [custom]\n", encoding="utf-8")
    calls: list[tuple[Path, bool]] = []

    def run_benchmark(path: Path, *, include_images: bool) -> dict[str, object]:
        calls.append((path, include_images))
        return {"dataset": "hotpotqa", "passed": True}

    monkeypatch.setattr(
        sys.modules[EvaluationDashboardService.__module__],
        "_run_benchmark_process",
        run_benchmark,
    )
    service = EvaluationDashboardService(
        _settings("golden.json"),
        FakeQueryEngine(),  # type: ignore[arg-type]
        settings_path,
    )

    report = service.run_hotpotqa_benchmark(include_images=False)

    assert report == {"dataset": "hotpotqa", "passed": True}
    assert calls == [(settings_path.resolve(), False)]
    assert service.hotpotqa_summary()["query_count"] == 120


def test_hotpotqa_job_exposes_running_state_until_background_run_finishes() -> None:
    release = Event()

    class BlockingEvaluationService:
        def run_hotpotqa_benchmark(self, *, include_images: bool = True) -> JsonDict:
            release.wait(timeout=2)
            return {"dataset": "hotpotqa", "passed": include_images}

    jobs = HotpotQABenchmarkJobService()
    job = jobs.submit(BlockingEvaluationService(), include_images=True)  # type: ignore[arg-type]

    assert job.active is True
    assert jobs.current() is not None
    with pytest.raises(ValueError, match="already running"):
        jobs.submit(BlockingEvaluationService(), include_images=False)  # type: ignore[arg-type]

    release.set()
    jobs.shutdown(wait=True)
    completed = jobs.current()
    assert completed is not None
    assert completed.status == "success"
    assert completed.report == {"dataset": "hotpotqa", "passed": True}


def _settings(golden_path: str) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25", "top_k_final": 5},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"], "golden_test_set": golden_path},
        observability={"enabled": False},
    )
