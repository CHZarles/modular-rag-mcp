from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.trace import TraceCollector
from core.types import QueryRequest, RetrievalCandidate
from observability.query_trace import run_traced_query


class FakeQueryEngine:
    def search(self, request: QueryRequest, trace: Any = None) -> list[RetrievalCandidate]:
        if trace is not None:
            trace.record_stage(
                "dense_retrieval",
                {
                    "method": "dense",
                    "provider": "FakeDense",
                    "details": {"candidates": []},
                },
                elapsed_ms=2.0,
            )
        return [
            RetrievalCandidate(
                chunk_id="chunk-a",
                text="answer",
                metadata={"source_path": "/tmp/guide.pdf"},
                score=0.8,
                source="rerank",
                rank=1,
            )
        ]


def test_run_traced_query_persists_request_and_final_ranking(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"

    results = run_traced_query(
        FakeQueryEngine(),  # type: ignore[arg-type]
        QueryRequest("stable identity", collection="docs", top_k=3),
        TraceCollector(path),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [result.chunk_id for result in results] == ["chunk-a"]
    assert payload["trace_type"] == "query"
    assert payload["metadata"]["query"] == "stable identity"
    assert payload["metadata"]["result_count"] == 1
    assert payload["metadata"]["results"][0]["chunk_id"] == "chunk-a"
