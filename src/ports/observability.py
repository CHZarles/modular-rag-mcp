"""Observability ports.

Business code depends on these small contracts; concrete sinks can write JSONL,
SQLite, OpenTelemetry, or any other backend without changing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.core.types import JsonDict


@dataclass(frozen=True)
class RunContext:
    run_id: str
    name: str
    run_type: str
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SpanContext:
    run_id: str
    span_id: str
    name: str
    span_type: str
    metadata: JsonDict = field(default_factory=dict)


@runtime_checkable
class BaseTracer(Protocol):
    def start_run(self, name: str, run_type: str, inputs: JsonDict) -> RunContext: ...
    def start_span(self, run_id: str, name: str, span_type: str, inputs: JsonDict) -> SpanContext: ...
    def record_event(
        self,
        run_id: str,
        span_id: str | None,
        event_type: str,
        payload: JsonDict,
    ) -> None: ...
    def record_artifact(
        self,
        run_id: str,
        span_id: str | None,
        artifact_type: str,
        content: JsonDict | str,
    ) -> None: ...


@runtime_checkable
class BaseTraceSink(Protocol):
    def on_run_started(self, run: JsonDict) -> None: ...
    def on_run_finished(self, run: JsonDict) -> None: ...
    def on_span_started(self, span: JsonDict) -> None: ...
    def on_span_finished(self, span: JsonDict) -> None: ...
    def on_event(self, event: JsonDict) -> None: ...
    def on_artifact(self, artifact: JsonDict) -> None: ...


@runtime_checkable
class BaseCacheStore(Protocol):
    def get(self, namespace: str, key: str) -> JsonDict | None: ...
    def set(
        self,
        namespace: str,
        key: str,
        value: JsonDict,
        ttl_seconds: int | None = None,
    ) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...
