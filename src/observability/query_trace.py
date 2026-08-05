"""Best-effort trace ownership around query entry points.

Production call sites go through ``LocalKnowledgeService.query`` and
``QueryKnowledgeHubTool``; this module owns the small helpers those paths
share so the compact Trace contract stays in one place:

* :func:`normalize_query` — Unicode-safe ``casefold`` + whitespace fold.
* :func:`sanitize_results` — strip ``source`` / ``source_path`` and cap at 20.
* :func:`classify_error_code` — map service exceptions to stable codes.
* :func:`build_query_trace_metadata` — assemble the §6.2 metadata block.

The legacy :func:`run_traced_query` helper stays for tests that exercise
the JSONL backend directly; production never uses it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from src.core.trace import TraceCollector, TraceContext
from src.core.types import JsonDict, QueryRequest, RetrievalCandidate
from src.mcp_server.request_context import RequestContext
from src.observability.ingestion_trace import create_trace_collector
from src.observability.logger import get_logger
from src.ports.query import QueryEngine

logger = get_logger(__name__)

_WHITESPACE_RE: Final = re.compile(r"\s+")
_COMPACT_RESULT_LIMIT: Final = 20
_KNOWN_ERROR_CODES: Final = frozenset(
    {"retrieval_failed", "internal_error", "query_failed"}
)


def normalize_query(query: str) -> str:
    """Plan §6.2: trim, fold whitespace, ``casefold``; keep Unicode verbatim."""
    if not isinstance(query, str):
        return ""
    folded = _WHITESPACE_RE.sub(" ", query.strip())
    return folded.casefold()


def sanitize_results(
    candidates: Iterable[RetrievalCandidate],
    *,
    limit: int = _COMPACT_RESULT_LIMIT,
) -> list[JsonDict]:
    """Build the compact ``metadata.results`` list per plan §6.2.

    Drops ``source`` and ``source_path`` (privacy §6.7) and keeps at most
    ``limit`` entries so a single trace row cannot blow up.
    """
    sanitized: list[JsonDict] = []
    for index, candidate in enumerate(candidates, start=1):
        metadata = candidate.metadata or {}
        sanitized.append(
            {
                "rank": candidate.rank if candidate.rank is not None else index,
                "doc_key": metadata.get("doc_key") or candidate.chunk_id,
                "chunk_id": candidate.chunk_id,
                "title": metadata.get("title"),
                "score": float(candidate.score) if candidate.score is not None else 0.0,
            }
        )
        if len(sanitized) >= limit:
            break
    return sanitized


def classify_error_code(exc: BaseException) -> str:
    """Map a service exception to one of the stable trace error codes.

    Keeps the trace payload aligned with plan §5.4 / §6.7 — no raw exception
    text leaks into the audit row, callers see only a stable identifier.
    """
    if isinstance(exc, LookupError):
        return "retrieval_failed"
    return "internal_error"


def build_query_trace_metadata(
    request: QueryRequest,
    request_context: RequestContext | None,
    *,
    status: str,
    error_code: str | None,
    result_count: int,
    results: list[JsonDict],
) -> JsonDict:
    """Assemble the §6.2 metadata block for a persisted Query Trace row."""
    if status not in {"success", "failed"}:
        raise ValueError(f"status must be 'success' or 'failed', got {status!r}")
    if error_code is not None and error_code not in _KNOWN_ERROR_CODES:
        raise ValueError(f"unknown error_code: {error_code!r}")
    if status == "success" and error_code is not None:
        raise ValueError("error_code must be null when status='success'")
    if status == "failed" and error_code is None:
        raise ValueError("error_code is required when status='failed'")
    request_id = request_context.request_id if request_context is not None else None
    actor_key = request_context.actor_key if request_context is not None else None
    session_id = request_context.session_id if request_context is not None else None
    zero_result = result_count == 0
    return {
        "request_id": request_id,
        "actor_key": actor_key,
        "session_id": session_id,
        "query": request.query,
        "query_normalized": normalize_query(request.query),
        "collection": request.collection,
        "top_k": request.top_k,
        "status": status,
        "error_code": error_code,
        "result_count": result_count,
        "zero_result": zero_result,
        "results": list(results),
    }


def run_traced_query(
    engine: QueryEngine,
    request: QueryRequest,
    collector: TraceCollector | None,
) -> list[RetrievalCandidate]:
    if collector is None:
        return engine.search(request)
    trace = TraceContext(
        trace_type="query",
        metadata={
            "query": request.query,
            "collection": request.collection,
            "top_k": request.top_k,
        },
    )
    try:
        results = engine.search(request, trace=trace)
    except Exception as exc:
        trace.metadata.update({"status": "failed", "error": str(exc)})
        _collect_safely(collector, trace)
        raise
    trace.metadata.update(
        {
            "status": "success",
            "result_count": len(results),
            "results": _candidate_snapshots(results),
        }
    )
    _collect_safely(collector, trace)
    return results


def _collect_safely(collector: TraceCollector, trace: TraceContext) -> None:
    try:
        collector.collect(trace)
    except Exception as exc:
        logger.warning("Unable to persist query trace %s: %s", trace.trace_id, exc)


def _candidate_snapshots(candidates: list[RetrievalCandidate]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": candidate.chunk_id,
            "rank": index,
            "score": candidate.score,
            "source": candidate.source,
            "source_path": candidate.metadata.get("source_path"),
            "title": candidate.metadata.get("title"),
        }
        for index, candidate in enumerate(candidates[:50], start=1)
    ]


__all__ = [
    "build_query_trace_metadata",
    "classify_error_code",
    "create_trace_collector",
    "normalize_query",
    "run_traced_query",
    "sanitize_results",
]
