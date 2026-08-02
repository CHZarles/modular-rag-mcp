"""可观测性端口契约。

业务代码只依赖这些精简契约；具体 Sink 可写入 JSONL、SQLite、OpenTelemetry 或
其他后端，而无需修改业务流水线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.core.types import JsonDict


@dataclass(frozen=True)
class RunContext:
    """一次顶层业务运行的标识与元数据。"""

    run_id: str
    name: str
    run_type: str
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SpanContext:
    """业务运行内部单个阶段的追踪上下文。"""

    run_id: str
    span_id: str
    name: str
    span_type: str
    metadata: JsonDict = field(default_factory=dict)


@runtime_checkable
class BaseTracer(Protocol):
    """创建 Run、Span 并记录事件或产物的追踪端口。"""

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
    """消费追踪生命周期事件的存储端口。"""

    def on_run_started(self, run: JsonDict) -> None: ...
    def on_run_finished(self, run: JsonDict) -> None: ...
    def on_span_started(self, span: JsonDict) -> None: ...
    def on_span_finished(self, span: JsonDict) -> None: ...
    def on_event(self, event: JsonDict) -> None: ...
    def on_artifact(self, artifact: JsonDict) -> None: ...


@runtime_checkable
class BaseCacheStore(Protocol):
    """按命名空间隔离且支持 TTL 的缓存端口。"""

    def get(self, namespace: str, key: str) -> JsonDict | None: ...
    def set(
        self,
        namespace: str,
        key: str,
        value: JsonDict,
        ttl_seconds: int | None = None,
    ) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...
