"""Compact Query Trace integration tests — plan §C2.3 / §6.2-§6.3.

These tests cover the full path from a Query Tool call down to the SQLite
row written by ``SQLiteTraceStore``. They replace the legacy JSONL fixture
in the same module because production no longer touches JSONL on the
request path; the legacy helper ``run_traced_query`` is exercised in
``test_ingestion_trace.py`` style coverage.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from src.core.response import ResponseBuilder
from src.core.trace import SQLiteTraceStore, TraceContext
from src.core.types import (
    Citation,
    JsonDict,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.mcp_server.request_context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool
from src.observability.query_trace import (
    build_query_trace_metadata,
    classify_error_code,
    normalize_query,
    sanitize_results,
)

# --- Helpers --------------------------------------------------------------


class _FakeService:
    def __init__(self, response: QueryResponse) -> None:
        self.response = response
        self.requests: list[QueryRequest] = []

    def query(self, request: QueryRequest, trace: Any = None) -> QueryResponse:
        self.requests.append(request)
        if trace is not None:
            trace.record_stage(
                "dense_retrieval",
                {
                    "method": "dense",
                    "provider": "FakeDense",
                    "status": "success",
                    "count": 2,
                    "details": {"candidates": ["alpha", "beta", "gamma"]},
                },
                elapsed_ms=1.7,
            )
            trace.record_stage(
                "sparse_retrieval",
                {
                    "method": "sparse",
                    "provider": "FakeSparse",
                    "status": "success",
                    "count": 1,
                    "details": {"candidates": ["alpha", "beta"]},
                },
                elapsed_ms=1.2,
            )
        return self.response


class _RaisingService:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.requests: list[QueryRequest] = []

    def query(self, request: QueryRequest, trace: Any = None) -> QueryResponse:
        self.requests.append(request)
        raise self.exc

    def list_collections(self):  # pragma: no cover
        raise AssertionError("not used")

    def get_document_summary(self, doc_id: str):  # pragma: no cover
        raise AssertionError("not used")


class _FlakyCollector:
    """Records calls but raises on collect() to verify best-effort wiring."""

    def __init__(self) -> None:
        self.records: list[Any] = []
        self.fail = True

    def collect(self, trace: Any) -> None:
        self.records.append(trace)
        if self.fail:
            raise RuntimeError("simulated collector outage")


def _candidate(
    chunk_id: str,
    *,
    score: float = 0.9,
    rank: int | None = None,
    metadata: JsonDict | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text for {chunk_id}",
        metadata={
            "source_path": f"/tmp/{chunk_id}.pdf",
            "title": f"Title {chunk_id}",
            **(metadata or {}),
        },
        score=score,
        source="dense",
        rank=rank if rank is not None else 1,
    )


def _response_with_items(items: list[RetrievalCandidate]) -> QueryResponse:
    citations = [
        Citation(
            citation_id=f"c{i}",
            chunk_id=item.chunk_id,
            source_path="manual.pdf",
            page=1,
            text="snippet",
            score=item.score,
        )
        for i, item in enumerate(items, start=1)
    ]
    return QueryResponse(
        answer="answer",
        citations=citations,
        items=items,
    )


# --- normalize_query ------------------------------------------------------


def test_normalize_query_applies_trim_collapse_casefold_keep_unicode() -> None:
    assert normalize_query("  Hello   WORLD  ") == "hello world"
    # Unicode letters casefold (ß → ss per Unicode, é stays é, Σ → σ);
    # whitespace folds to single ASCII space.
    assert normalize_query("\u00df\u00df\t H\u00e9llo  ") == "ssss h\u00e9llo"
    assert normalize_query("  \u03a3\u03a3  ") == "\u03c3\u03c3"
    # Non-string input degrades to "" so callers never crash.
    assert normalize_query("") == ""
    assert normalize_query(None) == ""  # type: ignore[arg-type]


# --- sanitize_results -----------------------------------------------------


def test_sanitize_results_caps_at_20_and_strips_internal_fields() -> None:
    candidates = [
        _candidate(f"c{i:02d}", score=0.9 - i * 0.01, rank=i + 1)
        for i in range(25)
    ]

    sanitized = sanitize_results(candidates)

    assert len(sanitized) == 20
    first = sanitized[0]
    # Internal fields stripped (privacy §6.7).
    assert set(first) == {"rank", "doc_key", "chunk_id", "title", "score"}
    assert first["rank"] == 1
    assert first["doc_key"] == "c00"
    assert first["title"] == "Title c00"
    # Last kept entry is the 20th candidate.
    assert sanitized[-1]["chunk_id"] == "c19"


def test_sanitize_results_falls_back_to_chunk_id_when_doc_key_missing() -> None:
    candidate = RetrievalCandidate(
        chunk_id="only-id",
        text="",
        metadata={},
        score=0.5,
        source="dense",
        rank=7,
    )
    sanitized = sanitize_results([candidate])
    assert sanitized[0]["doc_key"] == "only-id"
    assert sanitized[0]["rank"] == 7


# --- classify_error_code ---------------------------------------------------


def test_classify_error_code_returns_stable_codes() -> None:
    assert classify_error_code(LookupError("missing")) == "retrieval_failed"
    assert classify_error_code(KeyError("missing")) == "retrieval_failed"
    assert classify_error_code(RuntimeError("boom")) == "internal_error"


# --- build_query_trace_metadata -------------------------------------------


def test_build_query_trace_metadata_validates_status_and_error_code() -> None:
    request = QueryRequest(query="hi", collection="docs", top_k=3)
    with pytest.raises(ValueError, match="status must be"):
        build_query_trace_metadata(
            request, None, status="weird", error_code=None,
            result_count=0, results=[]
        )
    with pytest.raises(ValueError, match="unknown error_code"):
        build_query_trace_metadata(
            request, None, status="failed", error_code="oops",
            result_count=0, results=[]
        )
    with pytest.raises(ValueError, match="error_code must be null"):
        build_query_trace_metadata(
            request, None, status="success", error_code="retrieval_failed",
            result_count=1, results=[{"chunk_id": "x"}]
        )
    with pytest.raises(ValueError, match="error_code is required"):
        build_query_trace_metadata(
            request, None, status="failed", error_code=None,
            result_count=0, results=[]
        )


def test_build_query_trace_metadata_pulls_identity_from_context() -> None:
    request = QueryRequest(query="lease generation", collection="docs", top_k=3)
    ctx = RequestContext(request_id="req-9", actor_key="actor-1", session_id="sess-1")  # type: ignore[abstract]

    metadata = build_query_trace_metadata(
        request,
        ctx,
        status="success",
        error_code=None,
        result_count=1,
        results=[{"rank": 1, "doc_key": "k", "chunk_id": "c", "title": "T", "score": 0.9}],
    )

    assert metadata["request_id"] == "req-9"
    assert metadata["actor_key"] == "actor-1"
    assert metadata["session_id"] == "sess-1"
    assert metadata["query"] == "lease generation"
    assert metadata["query_normalized"] == "lease generation"
    assert metadata["status"] == "success"
    assert metadata["error_code"] is None
    assert metadata["result_count"] == 1
    assert metadata["zero_result"] is False


# --- End-to-end Query Tool integration ------------------------------------


def test_query_trace_writes_success_metadata_with_request_context(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    tool = QueryKnowledgeHubTool(
        get_service=lambda: _FakeService(_response_with_items([
            _candidate("alpha", rank=1),
            _candidate("beta", rank=2),
        ])),
        response_builder=ResponseBuilder(),
        get_collector=lambda: store,
    )
    ctx = RequestContext(request_id="req-7", actor_key="actor-2", session_id="sess-2")  # type: ignore[abstract]
    token = bind_request_context(ctx)
    try:
        result = tool.call({"query": "Lease  Generation", "top_k": 2, "collection": "docs"})
    finally:
        reset_request_context(token)

    # Response builder surfaces the trace_id for correlation.
    assert result["structuredContent"]["trace_id"] is not None

    rows = store.list_recent("query", limit=1)
    assert len(rows) == 1
    row = rows[0]
    metadata = row["metadata"]
    assert metadata["status"] == "success"
    assert metadata["error_code"] is None
    assert metadata["actor_key"] == "actor-2"
    assert metadata["session_id"] == "sess-2"
    assert metadata["request_id"] == "req-7"
    assert metadata["query"] == "Lease  Generation"
    assert metadata["query_normalized"] == "lease generation"
    assert metadata["result_count"] == 2
    assert metadata["zero_result"] is False
    assert metadata["collection"] == "docs"
    assert metadata["top_k"] == 2
    assert [entry["chunk_id"] for entry in metadata["results"]] == ["alpha", "beta"]
    # No raw exception text or answer body leaks into the row.
    payload_json = json.dumps(row, ensure_ascii=False)
    assert "RuntimeError" not in payload_json
    assert "text for alpha" not in payload_json


def test_query_trace_writes_zero_result_metadata_with_zero_result_true(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    tool = QueryKnowledgeHubTool(
        get_service=lambda: _FakeService(_response_with_items([])),
        get_collector=lambda: store,
    )

    tool.call({"query": "anything", "collection": "docs"})

    [row] = store.list_recent("query", limit=1)
    metadata = row["metadata"]
    assert metadata["status"] == "success"
    assert metadata["result_count"] == 0
    assert metadata["zero_result"] is True
    assert metadata["results"] == []


def test_query_trace_writes_failed_status_with_error_code_no_raw_text(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    service = _RaisingService(LookupError("missing collection"))
    tool = QueryKnowledgeHubTool(
        get_service=lambda: service,
        get_collector=lambda: store,
    )

    with pytest.raises(Exception) as excinfo:
        tool.call({"query": "lease", "collection": "docs"})

    # The Tool maps any service exception to the stable wire code.
    assert excinfo.value.component_code == "query_failed"  # type: ignore[attr-defined]

    [row] = store.list_recent("query", limit=1)
    metadata = row["metadata"]
    assert metadata["status"] == "failed"
    assert metadata["error_code"] == "retrieval_failed"
    assert metadata["result_count"] == 0
    assert metadata["zero_result"] is True
    payload_json = json.dumps(row, ensure_ascii=False)
    assert "missing collection" not in payload_json
    assert "LookupError" not in payload_json


def test_query_trace_collector_failure_does_not_break_query(
    tmp_path: pytest.TempPathFactory,
) -> None:
    flaky = _FlakyCollector()
    tool = QueryKnowledgeHubTool(
        get_service=lambda: _FakeService(_response_with_items([
            _candidate("alpha", rank=1),
        ])),
        get_collector=lambda: flaky,
    )

    # Collector raises; query still succeeds and trace_id is still stamped.
    result = tool.call({"query": "hi", "collection": "docs"})

    assert result["structuredContent"]["trace_id"] is not None
    assert flaky.records, "collector should still be invoked once"


# --- Stage filtering at serialize time -----------------------------------


def test_compact_trace_payload_omits_stage_candidates() -> None:
    trace = TraceContext(trace_type="query", detail="compact")
    trace.metadata.update({"status": "success"})
    trace.record_stage(
        "dense_retrieval",
        {
            "method": "dense",
            "provider": "FakeDense",
            "status": "success",
            "count": 2,
            "candidates": ["a", "b"],
            "details": {"candidates": ["x", "y"], "extra": "keep"},
        },
        elapsed_ms=1.0,
    )

    payload = trace.to_persisted_dict()

    assert payload["detail"] == "compact"
    assert payload["schema_version"] == 1
    dense = payload["stages"][0]
    # Per-stage flat candidates are dropped.
    assert "candidates" not in dense["data"]
    # Nested details.candidates are dropped but siblings stay.
    assert "candidates" not in dense["data"]["details"]
    assert dense["data"]["details"]["extra"] == "keep"
    # Other fields kept verbatim so the diagnostic UI can still render.
    assert dense["data"]["method"] == "dense"
    assert dense["data"]["status"] == "success"


def test_debug_trace_payload_caps_stage_candidates_at_20() -> None:
    trace = TraceContext(trace_type="query", detail="debug")
    trace.metadata.update({"status": "success"})
    trace.record_stage(
        "dense_retrieval",
        {
            "method": "dense",
            "provider": "FakeDense",
            "status": "success",
            "count": 30,
            "candidates": [f"c{i}" for i in range(30)],
            "details": {"candidates": [f"d{i}" for i in range(25)]},
        },
        elapsed_ms=1.0,
    )

    payload = trace.to_persisted_dict()

    assert payload["detail"] == "debug"
    dense = payload["stages"][0]
    assert len(dense["data"]["candidates"]) == 20
    assert len(dense["data"]["details"]["candidates"]) == 20
    # First 20 are retained.
    assert dense["data"]["candidates"][0] == "c0"
    assert dense["data"]["candidates"][-1] == "c19"


# --- Response surface -----------------------------------------------------


def test_query_response_carries_trace_id_in_structured_content() -> None:
    response = QueryResponse(answer="hi", citations=[], items=[])
    response = replace(response, trace_id="trace-xyz")
    payload = ResponseBuilder().build_mcp_result(response)
    assert payload["structuredContent"]["trace_id"] == "trace-xyz"
