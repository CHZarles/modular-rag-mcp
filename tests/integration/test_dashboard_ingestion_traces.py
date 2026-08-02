from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_ingestion_trace_page_renders_history_metrics_and_dynamic_stages(
    tmp_path: Path,
) -> None:
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(json.dumps(_trace()), encoding="utf-8")
    script = f"""\
from src.observability.dashboard.pages.ingestion_traces import render
from src.observability.dashboard.services import TraceService

render(TraceService({str(traces_path)!r}))
"""

    app = AppTest.from_string(script, default_timeout=20).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Ingestion 追踪"]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Runs", "1"),
        ("Success", "1"),
        ("Failed", "0"),
        ("Skipped", "0"),
        ("Status", "success"),
        ("Elapsed", "12.50 ms"),
        ("Chunks", "2"),
        ("Images", "1"),
    ]
    assert [expander.label for expander in app.expander] == [
        "load · pdf / PdfLoader · 3.50 ms",
        "embed · batch / OpenAI · 9.00 ms",
    ]


def _trace() -> dict[str, object]:
    return {
        "trace_id": "run-1",
        "trace_type": "ingestion",
        "started_at": "2026-08-02T01:00:00+00:00",
        "finished_at": "2026-08-02T01:00:01+00:00",
        "total_elapsed_ms": 12.5,
        "metadata": {
            "source_path": "/tmp/guide.pdf",
            "collection": "docs",
            "status": "success",
            "chunk_count": 2,
            "image_count": 1,
        },
        "stages": [
            {
                "stage": "load",
                "timestamp": "2026-08-02T01:00:00+00:00",
                "elapsed_ms": 3.5,
                "data": {
                    "method": "pdf",
                    "provider": "PdfLoader",
                    "details": {"output_count": 1},
                },
            },
            {
                "stage": "embed",
                "timestamp": "2026-08-02T01:00:00+00:00",
                "elapsed_ms": 9.0,
                "data": {
                    "method": "batch",
                    "provider": "OpenAI",
                    "details": {"output_count": 2},
                },
            },
        ],
    }
