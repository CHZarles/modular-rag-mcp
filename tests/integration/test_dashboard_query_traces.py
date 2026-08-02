from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_query_trace_page_renders_retrieval_and_rank_changes(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_text(json.dumps(_trace()), encoding="utf-8")
    script = f"""\
from src.observability.dashboard.pages.query_traces import render
from src.observability.dashboard.services import TraceService

render(TraceService({str(path)!r}))
"""

    app = AppTest.from_string(script, default_timeout=20).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Query 追踪"]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Status", "success"),
        ("Elapsed", "8.50 ms"),
        ("Results", "1"),
    ]
    assert len(app.dataframe) == 5


def _trace() -> dict[str, object]:
    candidate = {
        "chunk_id": "a",
        "rank": 1,
        "score": 0.8,
        "source": "dense",
        "source_path": "/tmp/guide.pdf",
    }
    return {
        "trace_id": "query-1",
        "trace_type": "query",
        "started_at": "2026-08-02T01:00:00+00:00",
        "finished_at": "2026-08-02T01:00:01+00:00",
        "total_elapsed_ms": 8.5,
        "metadata": {
            "query": "stable identity",
            "collection": "docs",
            "status": "success",
            "result_count": 1,
            "results": [candidate],
        },
        "stages": [
            _stage("dense_retrieval", "Dense", {"candidates": [candidate]}),
            _stage("sparse_retrieval", "BM25", {"candidates": [candidate]}),
            _stage(
                "rerank",
                "CrossEncoder",
                {"input_candidates": [candidate], "output_candidates": [candidate]},
            ),
        ],
    }


def _stage(name: str, provider: str, details: dict[str, object]) -> dict[str, object]:
    return {
        "stage": name,
        "timestamp": "2026-08-02T01:00:00+00:00",
        "elapsed_ms": 2.0,
        "data": {"method": name, "provider": provider, "details": details},
    }
