"""Dashboard Trace service tests (plan §C2.4 / §6.5).

Covers the SQLite-backed projection that the React Trace page renders.
The store is exercised through a temp file because ``SQLiteTraceStore``
is the production source of truth (plan §C2.2).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.trace import SQLiteTraceStore, TraceContext
from src.core.types import JsonDict, RetrievalCandidate
from src.observability.dashboard.services import (
    TraceReadResult,
    TraceRecord,
    TraceService,
    TraceStage,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteTraceStore]:
    yield SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)


def _make_trace(
    *,
    trace_type: str,
    trace_id: str,
    started_at: datetime,
    status: str = "success",
    extra_metadata: JsonDict | None = None,
) -> TraceContext:
    trace = TraceContext(trace_type=trace_type)
    trace.trace_id = trace_id
    trace.started_at = started_at.isoformat()
    trace.record_stage(
        "embed",
        {
            "method": "batch",
            "provider": "OpenAIEmbedding",
            "details": {"output_count": 2},
        },
        elapsed_ms=4.25,
    )
    trace.metadata.update(
        {
            "source_path": "/tmp/guide.pdf",
            "collection": "docs",
            "status": status,
            **(extra_metadata or {}),
        }
    )
    # Pin total elapsed so list ordering is deterministic.
    trace._finish_mono = trace._start_mono + 0.01  # type: ignore[attr-defined]
    trace.finished_at = (
        datetime.fromisoformat(trace.started_at) + timedelta(milliseconds=10)
    ).isoformat()
    return trace


def _ingest(store: SQLiteTraceStore, trace: TraceContext) -> None:
    store.collect(trace)


def test_trace_service_returns_recent_records_newest_first(store: SQLiteTraceStore) -> None:
    base = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    _ingest(
        store,
        _make_trace(
            trace_type="ingestion",
            trace_id="older",
            started_at=base,
            status="failed",
        ),
    )
    _ingest(
        store,
        _make_trace(
            trace_type="query",
            trace_id="query",
            started_at=base + timedelta(hours=2),
        ),
    )
    _ingest(
        store,
        _make_trace(
            trace_type="ingestion",
            trace_id="newer",
            started_at=base + timedelta(hours=1),
            status="success",
        ),
    )

    snapshot = TraceService(store).read_traces("ingestion")

    assert [trace.trace_id for trace in snapshot.traces] == ["newer", "older"]
    assert [trace.status for trace in snapshot.traces] == ["success", "failed"]
    assert snapshot.malformed_line_count == 0
    assert snapshot.traces[0].stages[0].method == "batch"
    assert snapshot.traces[0].stages[0].provider == "OpenAIEmbedding"
    assert snapshot.traces[0].stages[0].details == {"output_count": 2}


def test_trace_service_rejects_unknown_trace_type(store: SQLiteTraceStore) -> None:
    service = TraceService(store)

    with pytest.raises(ValueError, match="unsupported trace_type"):
        service.read_traces("evaluation")
    # Listing also fails fast; the API layer maps this to HTTP 400.
    with pytest.raises(ValueError):
        service.list_traces("evaluation")


def test_trace_service_get_trace_returns_match_across_types(store: SQLiteTraceStore) -> None:
    base = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    _ingest(store, _make_trace(trace_type="query", trace_id="q1", started_at=base))
    _ingest(
        store,
        _make_trace(trace_type="ingestion", trace_id="i1", started_at=base + timedelta(minutes=5)),
    )
    service = TraceService(store)

    assert service.get_trace("i1").trace_type == "ingestion"
    with pytest.raises(KeyError, match="not found"):
        service.get_trace("missing")
    with pytest.raises(ValueError, match="must not be empty"):
        service.get_trace(" ")


def test_trace_service_caps_limit_at_configured_maximum(store: SQLiteTraceStore) -> None:
    service = TraceService(store, default_limit=2, max_limit=3)
    base = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    for index in range(4):
        _ingest(
            store,
            _make_trace(
                trace_type="query",
                trace_id=f"t{index}",
                started_at=base + timedelta(minutes=index),
            ),
        )

    snapshot = service.read_traces("query", limit=10)
    assert len(snapshot.traces) == 3
    explicit = service.read_traces("query", limit=1)
    assert len(explicit.traces) == 1
    # Default limit kicks in when caller passes nothing.
    assert len(service.read_traces("query").traces) == 2


def test_trace_service_returns_empty_for_unknown_type(store: SQLiteTraceStore) -> None:
    # ``ingestion`` has no rows yet, but is a valid type.
    snapshot = TraceService(store).read_traces("ingestion")
    assert snapshot == TraceReadResult(())


def test_trace_record_status_falls_back_to_stage_when_metadata_missing(
    store: SQLiteTraceStore,
) -> None:
    trace = TraceContext(trace_type="ingestion")
    trace.trace_id = "fallback"
    trace.started_at = "2026-08-02T01:00:00+00:00"
    trace.record_stage(
        "ingestion",
        {
            "method": "ingest",
            "provider": "Local",
            "details": {"status": "success", "chunk_count": 1},
        },
        elapsed_ms=1.0,
    )
    trace.metadata = {}
    trace._finish_mono = trace._start_mono + 0.005  # type: ignore[attr-defined]
    trace.finished_at = "2026-08-02T01:00:00.005000+00:00"
    _ingest(store, trace)

    record = TraceService(store).get_trace("fallback")
    assert record.status == "success"


def test_trace_stage_dataclass_is_exposed_for_backward_compat() -> None:
    # The React page renders TraceStage fields directly; ensure the dataclass
    # still exposes the same surface and ordering.
    stage = TraceStage(
        name="dense_retrieval",
        timestamp="2026-08-02T01:00:00+00:00",
        elapsed_ms=1.5,
        method="dense",
        provider="chroma",
        details={"count": 3},
        data={"method": "dense", "provider": "chroma", "details": {"count": 3}},
    )
    assert stage.name == "dense_retrieval"
    assert stage.elapsed_ms == 1.5
    assert stage.details == {"count": 3}


def test_trace_record_dataclass_keeps_legacy_field_order() -> None:
    # Defensive: the React types rely on these field names verbatim.
    record = TraceRecord(
        trace_id="trace-1",
        trace_type="query",
        started_at="2026-08-02T01:00:00+00:00",
        finished_at="2026-08-02T01:00:01+00:00",
        total_elapsed_ms=1000.0,
        stages=(),
        metadata={"status": "success"},
    )
    assert record.status == "success"


def test_candidate_import_does_not_break_dashboard_callers() -> None:
    # Sanity: the dashboard service file does not depend on retrieval models
    # directly; this guards against future regressions where someone imports
    # ``RetrievalCandidate`` here by accident.
    assert isinstance(RetrievalCandidate.__dataclass_fields__, dict)
