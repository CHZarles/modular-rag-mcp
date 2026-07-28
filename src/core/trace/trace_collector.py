"""链路追踪记录收集器。"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.trace.trace_context import TraceContext


class JsonlTraceCollector:
    """以追加方式写入 JSON Lines，保持追踪组件轻量可选。"""

    def __init__(self, traces_path: str | Path) -> None:
        self._path = Path(traces_path)

    @property
    def path(self) -> Path:
        return self._path

    def collect(self, trace: TraceContext) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
