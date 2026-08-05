"""Read-only projection over the SQLite Trace store for Dashboard views.

Plan §C2.4: replaces the JSONL reader but keeps ``TraceRecord`` /
``TraceStage`` / ``TraceReadResult`` unchanged so the React Trace page
keeps rendering without any front-end changes.

The service is a thin adapter: ``SQLiteTraceStore`` owns durability and
the row-level contract, this module only enforces the Dashboard's
``trace_type`` allow-list and the ``limit`` cap mandated by §6.5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.trace import SQLiteTraceStore
from src.core.types import JsonDict

_ALLOWED_TRACE_TYPES: frozenset[str] = frozenset({"query", "ingestion"})
_DEFAULT_LIMIT: int = 200
_MAX_LIMIT: int = 1000


@dataclass(frozen=True)
class TraceStage:
    """Display-ready stage parsed from one trace record."""

    name: str
    timestamp: str
    elapsed_ms: float | None
    method: str
    provider: str
    details: JsonDict = field(default_factory=dict)
    data: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class TraceRecord:
    """Validated trace record independent from the live TraceContext object."""

    trace_id: str
    trace_type: str
    started_at: str
    finished_at: str | None
    total_elapsed_ms: float
    stages: tuple[TraceStage, ...]
    metadata: JsonDict = field(default_factory=dict)

    @property
    def status(self) -> str:
        metadata_status = self.metadata.get("status")
        if isinstance(metadata_status, str) and metadata_status:
            return metadata_status
        for stage in reversed(self.stages):
            status = stage.details.get("status")
            if stage.name == "ingestion" and isinstance(status, str) and status:
                return status
        if any(stage.details.get("status") == "error" for stage in self.stages):
            return "failed"
        return "unknown"


@dataclass(frozen=True)
class TraceReadResult:
    """One point-in-time read; SQLite stores never produce malformed rows."""

    traces: tuple[TraceRecord, ...]
    malformed_line_count: int = 0


class TraceService:
    """Surface recent traces from the SQLite Trace store to the Dashboard."""

    def __init__(
        self,
        store: SQLiteTraceStore,
        *,
        default_limit: int = _DEFAULT_LIMIT,
        max_limit: int = _MAX_LIMIT,
    ) -> None:
        self._store = store
        self._default_limit = _coerce_limit(default_limit, default=True)
        self._max_limit = _coerce_limit(max_limit, default=False)

    @property
    def store(self) -> SQLiteTraceStore:
        return self._store

    def read_traces(
        self,
        trace_type: str,
        *,
        limit: int | None = None,
    ) -> TraceReadResult:
        """Return at most ``limit`` recent traces of the given type.

        ``limit`` defaults to the configured ceiling (200) and is hard-capped
        at ``max_limit`` so an HTTP caller cannot ask for an unbounded scan.
        """
        if trace_type not in _ALLOWED_TRACE_TYPES:
            raise ValueError(
                f"unsupported trace_type: {trace_type!r} "
                f"(allowed: {sorted(_ALLOWED_TRACE_TYPES)})"
            )
        effective = self._coerce_effective_limit(limit)
        payloads = self._store.list_recent(trace_type, effective)
        records = tuple(_payload_to_record(payload) for payload in payloads)
        return TraceReadResult(records, malformed_line_count=0)

    def list_traces(
        self,
        trace_type: str,
        *,
        limit: int | None = None,
    ) -> list[TraceRecord]:
        return list(self.read_traces(trace_type, limit=limit).traces)

    def get_trace(self, trace_id: str) -> TraceRecord:
        """Return the most recent trace matching ``trace_id``.

        Looks up across ``query`` + ``ingestion`` because the Dashboard does
        not carry a ``trace_type`` discriminator on direct lookups.
        """
        normalized = trace_id.strip()
        if not normalized:
            raise ValueError("trace_id must not be empty")
        for trace_type in _ALLOWED_TRACE_TYPES:
            for trace in self.list_traces(trace_type):
                if trace.trace_id == normalized:
                    return trace
        raise KeyError(f"trace not found: {normalized}")

    def _coerce_effective_limit(self, limit: int | None) -> int:
        if limit is None:
            return self._default_limit
        # Caller-supplied limits are silently clamped to the configured max;
        # the API layer is supposed to ignore arbitrary client limits anyway.
        if isinstance(limit, bool) or not isinstance(limit, int):
            return self._default_limit
        if limit < 1:
            return self._default_limit
        if limit > self._max_limit:
            return self._max_limit
        return limit


def _payload_to_record(payload: JsonDict) -> TraceRecord:
    trace_id = _required_text(payload, "trace_id")
    trace_type = _required_text(payload, "trace_type")
    started_at = _required_text(payload, "started_at")
    _parse_timestamp(started_at)
    finished_at = payload.get("finished_at")
    if finished_at is not None:
        if not isinstance(finished_at, str):
            raise ValueError("finished_at must be text or null")
        _parse_timestamp(finished_at)
    stages_raw = payload.get("stages")
    metadata_raw = payload.get("metadata", {})
    if not isinstance(stages_raw, list) or not isinstance(metadata_raw, dict):
        raise ValueError("trace stages and metadata have invalid types")
    return TraceRecord(
        trace_id=trace_id,
        trace_type=trace_type,
        started_at=started_at,
        finished_at=finished_at,
        total_elapsed_ms=_required_nonnegative_number(
            payload.get("total_elapsed_ms"), "total_elapsed_ms"
        ),
        stages=tuple(_payload_to_stage(stage) for stage in stages_raw),
        metadata=dict(metadata_raw),
    )


def _payload_to_stage(payload: Any) -> TraceStage:
    if not isinstance(payload, dict):
        raise ValueError("trace stage must be an object")
    name = _required_text(payload, "stage")
    timestamp = _required_text(payload, "timestamp")
    _parse_timestamp(timestamp)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("trace stage data must be an object")
    details = data.get("details", {})
    return TraceStage(
        name=name,
        timestamp=timestamp,
        elapsed_ms=_optional_nonnegative_number(payload.get("elapsed_ms")),
        method=_optional_text(data.get("method"), "unknown"),
        provider=_optional_text(data.get("provider"), "unknown"),
        details=dict(details) if isinstance(details, dict) else {},
        data=dict(data),
    )


def _required_text(payload: JsonDict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"trace field must be non-empty text: {key}")
    return value.strip()


def _optional_text(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _required_nonnegative_number(value: Any, field: str) -> float:
    parsed = _optional_nonnegative_number(value)
    if parsed is None:
        raise ValueError(f"trace field must be a non-negative number: {field}")
    return parsed


def _optional_nonnegative_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("trace elapsed time must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("trace elapsed time must be finite and non-negative")
    return parsed


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid trace timestamp: {value}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _coerce_limit(value: int, *, default: bool, ceiling: int | None = None) -> int:
    """Strict coercion used for service constructor arguments."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be a positive integer")
    floor = 1
    cap = ceiling if ceiling is not None else _MAX_LIMIT
    if value < floor:
        if default:
            return _DEFAULT_LIMIT
        raise ValueError(f"limit must be at least {floor}")
    if value > cap:
        if default:
            return _DEFAULT_LIMIT
        raise ValueError(f"limit must be at most {cap}")
    return value


__all__ = [
    "TraceReadResult",
    "TraceRecord",
    "TraceService",
    "TraceStage",
    "_ALLOWED_TRACE_TYPES",
]
