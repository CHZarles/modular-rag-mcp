"""Trace collectors."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.trace.trace_context import TraceContext


class JsonlTraceCollector:
    """Append traces to JSON Lines without making tracing a hard dependency."""

    def __init__(self, traces_path: str | Path) -> None:
        self._path = Path(traces_path)

    @property
    def path(self) -> Path:
        return self._path

    def collect(self, trace: TraceContext) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
