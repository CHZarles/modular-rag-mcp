"""TraceContext 生命周期、耗时统计和收集持久化测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.core.trace import JSONLTraceCollector, TraceContext


def test_trace_context_defaults_to_query_and_records_ordered_stages() -> None:
    trace = TraceContext()

    trace.record_stage("query_processing", {"method": "normalize"}, elapsed_ms=1.25)
    trace.record_stage("fusion", {"method": "rrf"})

    assert trace.trace_type == "query"
    assert trace.finished_at is None
    assert [stage["stage"] for stage in trace.stages] == ["query_processing", "fusion"]
    assert trace.elapsed_ms("query_processing") == 1.25
    assert trace.get_stage_data("fusion") == {"method": "rrf"}


def test_stage_timer_records_payload_and_elapsed_time() -> None:
    trace = TraceContext(trace_type="ingestion")

    with trace.stage_timer("load") as payload:
        payload["method"] = "markitdown"

    stage = trace.stages[0]
    assert stage["data"] == {"method": "markitdown"}
    assert isinstance(stage["elapsed_ms"], float)
    assert stage["elapsed_ms"] >= 0


def test_elapsed_ms_rejects_stage_without_recorded_timing() -> None:
    trace = TraceContext()
    trace.record_stage("fusion", {"method": "rrf"})

    with pytest.raises(KeyError, match="fusion"):
        trace.elapsed_ms("fusion")


def test_finish_freezes_total_elapsed_time_and_is_idempotent() -> None:
    trace = TraceContext()
    time.sleep(0.001)

    trace.finish()
    first_finished_at = trace.finished_at
    first_elapsed = trace.elapsed_ms()
    time.sleep(0.001)
    trace.finish()

    assert trace.finished_at == first_finished_at
    assert trace.elapsed_ms() == first_elapsed


def test_to_dict_contains_finished_json_serializable_trace() -> None:
    trace = TraceContext(trace_type="ingestion", metadata={"collection": "docs"})
    trace.record_stage("load", {"document_count": 1}, elapsed_ms=2.5)
    trace.finish()

    payload = trace.to_dict()
    restored = json.loads(json.dumps(payload))

    assert restored["trace_id"] == trace.trace_id
    assert restored["trace_type"] == "ingestion"
    assert restored["started_at"] == trace.started_at
    assert restored["finished_at"] == trace.finished_at
    assert isinstance(restored["total_elapsed_ms"], float)
    assert restored["stages"][0]["elapsed_ms"] == 2.5
    assert restored["metadata"] == {"collection": "docs"}


def test_trace_collector_finishes_and_appends_json_lines(tmp_path: Path) -> None:
    traces_path = tmp_path / "nested" / "traces.jsonl"
    collector = JSONLTraceCollector(traces_path)
    first = TraceContext(trace_type="query")
    second = TraceContext(trace_type="ingestion")

    collector.collect(first)
    collector.collect(second)

    lines = traces_path.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]
    assert first.finished_at is not None
    assert second.finished_at is not None
    assert [payload["trace_type"] for payload in payloads] == ["query", "ingestion"]
    assert all(payload["finished_at"] is not None for payload in payloads)
    assert collector.path == traces_path
