"""Best-effort trace ownership around query entry points."""

from __future__ import annotations

from src.core.trace import TraceCollector, TraceContext
from src.core.types import QueryRequest, RetrievalCandidate
from src.observability.ingestion_trace import create_trace_collector
from src.observability.logger import get_logger
from src.ports.query import QueryEngine

logger = get_logger(__name__)


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


__all__ = ["create_trace_collector", "run_traced_query"]
