from __future__ import annotations

import json
from pathlib import Path

import pytest

from observability.dashboard.services import TraceService


def test_trace_service_filters_sorts_and_parses_dynamic_stage_fields(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    lines = [
        _trace("older", "ingestion", "2026-08-02T01:00:00+00:00", status="failed"),
        _trace("query", "query", "2026-08-02T03:00:00+00:00", status="success"),
        _trace("newer", "ingestion", "2026-08-02T02:00:00+00:00", status="success"),
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    snapshot = TraceService(path).read_traces("ingestion")

    assert [trace.trace_id for trace in snapshot.traces] == ["newer", "older"]
    assert [trace.status for trace in snapshot.traces] == ["success", "failed"]
    assert snapshot.traces[0].stages[0].method == "batch"
    assert snapshot.traces[0].stages[0].provider == "OpenAIEmbedding"
    assert snapshot.traces[0].stages[0].details == {"output_count": 2}
    assert snapshot.malformed_line_count == 0


def test_trace_service_skips_partial_and_schema_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_text(
        json.dumps(_trace("valid", "ingestion", "2026-08-02T02:00:00+00:00"))
        + '\n{"trace_id":\n'
        + json.dumps({"trace_id": "missing-fields"})
        + "\n",
        encoding="utf-8",
    )

    snapshot = TraceService(path).read_traces()

    assert [trace.trace_id for trace in snapshot.traces] == ["valid"]
    assert snapshot.malformed_line_count == 2


def test_trace_service_handles_missing_file_and_exact_lookup(tmp_path: Path) -> None:
    service = TraceService(tmp_path / "missing.jsonl")

    assert service.list_traces("ingestion") == []
    with pytest.raises(KeyError, match="not found"):
        service.get_trace("missing")
    with pytest.raises(ValueError, match="must not be empty"):
        service.get_trace(" ")


def _trace(
    trace_id: str,
    trace_type: str,
    started_at: str,
    *,
    status: str = "success",
) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "trace_type": trace_type,
        "started_at": started_at,
        "finished_at": "2026-08-02T03:00:00+00:00",
        "total_elapsed_ms": 8.5,
        "metadata": {
            "source_path": "/tmp/guide.pdf",
            "collection": "docs",
            "status": status,
        },
        "stages": [
            {
                "stage": "embed",
                "timestamp": started_at,
                "elapsed_ms": 4.25,
                "data": {
                    "method": "batch",
                    "provider": "OpenAIEmbedding",
                    "details": {"output_count": 2},
                },
            }
        ],
    }
