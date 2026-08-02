"""Best-effort trace ownership around ingestion entry points."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.services import IngestionService
from src.core.settings import Settings
from src.core.trace import TraceCollector, TraceContext
from src.core.types import IngestionRequest, IngestionResult, ProgressCallback
from src.observability.logger import get_logger

logger = get_logger(__name__)


def create_trace_collector(settings: Settings) -> TraceCollector | None:
    """Build the configured collector, or return None when tracing is disabled."""
    observability = settings.observability
    if not _boolean(observability.get("enabled"), default=True):
        return None
    path = _required_text(observability, "log_file", "observability")
    return TraceCollector(path)


create_ingestion_trace_collector = create_trace_collector


def run_traced_ingestion(
    ingestion: IngestionService,
    request: IngestionRequest,
    collector: TraceCollector | None,
    on_progress: ProgressCallback | None = None,
) -> IngestionResult:
    """Run ingestion and persist diagnostics without changing its business result."""
    if collector is None:
        return ingestion.ingest(request, on_progress=on_progress)

    trace = TraceContext(
        trace_type="ingestion",
        metadata={
            "source_path": request.source_path,
            "collection": request.collection,
            "force": request.force,
            "request_id": request.request_id,
        },
    )
    try:
        result = ingestion.ingest(request, on_progress=on_progress, trace=trace)
    except Exception as exc:
        trace.metadata.update({"status": "failed", "error": str(exc)})
        trace.record_stage(
            "ingestion",
            {
                "method": "ingest",
                "provider": type(ingestion).__name__,
                "details": {"status": "failed", "error": str(exc)},
            },
        )
        _collect_safely(collector, trace)
        raise

    trace.metadata.update(
        {
            "source_path": result.source_path,
            "collection": result.collection,
            "status": result.status,
            "chunk_count": result.chunk_count,
            "image_count": result.image_count,
            "error": result.error,
        }
    )
    _collect_safely(collector, trace)
    return result


def _collect_safely(collector: TraceCollector, trace: TraceContext) -> None:
    try:
        collector.collect(trace)
    except Exception as exc:
        logger.warning("Unable to persist ingestion trace %s: %s", trace.trace_id, exc)


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"observability configuration error: invalid boolean {value!r}")


__all__ = [
    "create_ingestion_trace_collector",
    "create_trace_collector",
    "run_traced_ingestion",
]
