from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.settings import Settings
from core.types import QueryRequest, RetrievalCandidate
from observability.dashboard.services import EvaluationDashboardService


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
