"""Best-effort trace ownership around ingestion entry points (plan §C2.2)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.application.services import IngestionService
from src.core.settings import Settings
from src.core.trace import (
    JSONLTraceCollector,
    SQLiteTraceStore,
    TraceCollector,
    TraceContext,
    TraceDetail,
)
from src.core.types import IngestionRequest, IngestionResult, ProgressCallback
from src.observability.logger import get_logger

logger = get_logger(__name__)

_BACKEND_SQLITE = "sqlite"
_BACKEND_JSONL = "jsonl"
_SUPPORTED_BACKENDS: frozenset[str] = frozenset({_BACKEND_SQLITE, _BACKEND_JSONL})
_DEFAULT_TRACE_DB_PATH = Path("./data/db/traces.db")
_DEFAULT_INTEGRITY_DB_PATH = Path("./data/db/ingestion_history.db")


def create_trace_collector(settings: Settings) -> TraceCollector | None:
    """Build the configured collector, or return ``None`` when disabled."""
    observability = settings.observability
    if not _boolean(observability.get("enabled"), default=True):
        return None
    backend = _backend(observability)
    if backend == _BACKEND_JSONL:
        path = _required_text(observability, "log_file", "observability")
        return JSONLTraceCollector(path)
    # SQLite is the production path (plan §11.1).
    return _build_sqlite_store(settings, observability)


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


def _build_sqlite_store(
    settings: Settings, observability: Mapping[str, Any]
) -> SQLiteTraceStore:
    retention_days = _retention_days(observability)
    detail = _detail(observability)
    path = _trace_db_path(observability, settings)
    integrity_path = _ingestion_integrity_path(settings)
    store = SQLiteTraceStore(
        path,
        retention_days=retention_days,
        detail=detail,
        ingest_integrity_path=integrity_path,
        auto_purge=True,
    )
    # Run once-per-24h housekeeping; off in tests that pass auto_purge=False.
    store.maybe_auto_purge()
    return store


def _trace_db_path(
    observability: Mapping[str, Any], settings: Settings
) -> Path:
    raw = observability.get("trace_db_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return _DEFAULT_TRACE_DB_PATH


def _ingestion_integrity_path(settings: Settings) -> Path:
    storage = settings.ingestion.get("storage")
    if isinstance(storage, Mapping):
        raw = storage.get("integrity_db_path")
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser()
    return _DEFAULT_INTEGRITY_DB_PATH


def _backend(observability: Mapping[str, Any]) -> str:
    raw = observability.get("backend")
    if raw is None:
        return _BACKEND_SQLITE
    if not isinstance(raw, str):
        raise ValueError("observability.backend must be a string")
    normalized = raw.strip().lower()
    if not normalized:
        return _BACKEND_SQLITE
    if normalized not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"observability.backend must be one of {sorted(_SUPPORTED_BACKENDS)}, "
            f"got {raw!r}"
        )
    return normalized


def _retention_days(observability: Mapping[str, Any]) -> int:
    raw = observability.get("retention_days", 90)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("observability.retention_days must be an integer")
    if not (1 <= raw <= 3650):
        raise ValueError(
            "observability.retention_days must be between 1 and 3650"
        )
    return raw


def _detail(observability: Mapping[str, Any]) -> TraceDetail:
    raw = observability.get("detail", "compact")
    if not isinstance(raw, str):
        raise ValueError("observability.detail must be a string")
    normalized = raw.strip().lower()
    if normalized not in {"compact", "debug"}:
        raise ValueError(
            "observability.detail must be either 'compact' or 'debug'"
        )
    return normalized  # type: ignore[return-value]


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
