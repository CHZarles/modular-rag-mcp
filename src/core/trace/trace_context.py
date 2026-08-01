"""面向单次请求的本地可观测性追踪上下文。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from src.core.types import JsonDict


@dataclass
class TraceContext:
    """记录一次请求的阶段数据与单调时钟耗时。"""

    trace_type: Literal["query", "ingestion", "evaluation", "management"] = "query"
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    stages: list[JsonDict] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    _start_mono: float = field(default_factory=time.monotonic, repr=False)
    _finish_mono: float | None = field(default=None, repr=False)
    _stage_timings: dict[str, float] = field(default_factory=dict, repr=False)

    def record_stage(
        self,
        stage_name: str,
        data: JsonDict,
        elapsed_ms: float | None = None,
    ) -> None:
        entry: JsonDict = {
            "stage": stage_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        if elapsed_ms is not None:
            entry["elapsed_ms"] = round(elapsed_ms, 2)
            self._stage_timings[stage_name] = elapsed_ms
        self.stages.append(entry)

    @contextmanager
    def stage_timer(self, stage_name: str, data: JsonDict | None = None) -> Iterator[JsonDict]:
        """以上下文管理器形式记录一个阶段，即使异常也会写入耗时。"""
        payload = data if data is not None else {}
        started = time.monotonic()
        try:
            yield payload
        finally:
            self.record_stage(stage_name, payload, (time.monotonic() - started) * 1000.0)

    def finish(self) -> None:
        """结束追踪；重复调用不会改变首次结束时间和总耗时。"""
        if self._finish_mono is not None:
            return
        self._finish_mono = time.monotonic()
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def elapsed_ms(self, stage_name: str | None = None) -> float:
        """返回指定阶段或整条链路的毫秒耗时。"""
        if stage_name is not None:
            if stage_name not in self._stage_timings:
                raise KeyError(f"stage has no recorded timing: {stage_name}")
            return self._stage_timings[stage_name]
        end = self._finish_mono if self._finish_mono is not None else time.monotonic()
        return (end - self._start_mono) * 1000.0

    def get_stage_data(self, stage_name: str) -> JsonDict | None:
        """返回同名阶段最近一次记录的数据。"""
        for entry in reversed(self.stages):
            if entry.get("stage") == stage_name:
                data = entry.get("data")
                return data if isinstance(data, dict) else None
        return None

    def to_dict(self) -> JsonDict:
        """转换为可直接交给 ``json.dumps`` 的普通字典。"""
        return {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_elapsed_ms": round(self.elapsed_ms(), 2),
            "stages": list(self.stages),
            "metadata": dict(self.metadata),
        }
