"""Resilient JSON Lines reader for Dashboard trace views."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.types import JsonDict


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
    """One point-in-time file read, including non-fatal parse diagnostics."""

    traces: tuple[TraceRecord, ...]
    malformed_line_count: int = 0


class TraceService:
    """Read complete JSONL records while tolerating malformed or partial lines."""

    def __init__(self, traces_path: str | Path) -> None:
        self.path = Path(traces_path).expanduser()

    def read_traces(self, trace_type: str | None = None) -> TraceReadResult:
        if not self.path.is_file():
            return TraceReadResult(())

        traces: list[TraceRecord] = []
        malformed = 0
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    trace = _parse_trace(payload)
                except (json.JSONDecodeError, TypeError, ValueError):
                    malformed += 1
                    continue
                if trace_type is None or trace.trace_type == trace_type:
                    traces.append(trace)

        traces.sort(key=lambda trace: _timestamp(trace.started_at), reverse=True)
        return TraceReadResult(tuple(traces), malformed)

    def list_traces(self, trace_type: str | None = None) -> list[TraceRecord]:
        return list(self.read_traces(trace_type).traces)

    def get_trace(self, trace_id: str) -> TraceRecord:
        normalized = trace_id.strip()
        if not normalized:
            raise ValueError("trace_id must not be empty")
        for trace in self.read_traces().traces:
            if trace.trace_id == normalized:
                return trace
        raise KeyError(f"trace not found: {normalized}")


def _parse_trace(payload: Any) -> TraceRecord:
    if not isinstance(payload, dict):
        raise TypeError("trace must be an object")
    trace_id = _required_text(payload, "trace_id")
    trace_type = _required_text(payload, "trace_type")
    started_at = _required_text(payload, "started_at")
    _timestamp(started_at)
    finished_at = payload.get("finished_at")
    if finished_at is not None:
        if not isinstance(finished_at, str):
            raise TypeError("finished_at must be text or null")
        _timestamp(finished_at)
    stages = payload.get("stages")
    metadata = payload.get("metadata", {})
    if not isinstance(stages, list) or not isinstance(metadata, dict):
        raise TypeError("trace stages and metadata have invalid types")
    return TraceRecord(
        trace_id=trace_id,
        trace_type=trace_type,
        started_at=started_at,
        finished_at=finished_at,
        total_elapsed_ms=_nonnegative_number(payload.get("total_elapsed_ms")),
        stages=tuple(_parse_stage(stage) for stage in stages),
        metadata=dict(metadata),
    )


def _parse_stage(payload: Any) -> TraceStage:
    if not isinstance(payload, dict):
        raise TypeError("trace stage must be an object")
    name = _required_text(payload, "stage")
    timestamp = _required_text(payload, "timestamp")
    _timestamp(timestamp)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TypeError("trace stage data must be an object")
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


def _nonnegative_number(value: Any) -> float:
    parsed = _optional_nonnegative_number(value)
    if parsed is None:
        raise ValueError("trace elapsed time must be a non-negative number")
    return parsed


def _optional_nonnegative_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("trace elapsed time must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("trace elapsed time must be finite and non-negative")
    return parsed


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid trace timestamp: {value}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


__all__ = ["TraceReadResult", "TraceRecord", "TraceService", "TraceStage"]
