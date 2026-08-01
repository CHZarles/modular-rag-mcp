"""链路追踪记录收集器。"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.trace.trace_context import TraceContext


class TraceCollector:
    """结束并持久化 Trace，每条记录占一个 JSON Lines 行。"""

    def __init__(self, traces_path: str | Path = "logs/traces.jsonl") -> None:
        self._path = Path(traces_path)

    @property
    def path(self) -> Path:
        return self._path

    def collect(self, trace: TraceContext) -> None:
        trace.finish()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


class JsonlTraceCollector(TraceCollector):
    """保留原有名称，明确该 Collector 当前使用 JSON Lines 后端。"""
