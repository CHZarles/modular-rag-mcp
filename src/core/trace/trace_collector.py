"""Trace collection surface — minimal Protocol plus concrete implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.core.trace.sqlite_trace_store import SQLiteTraceStore  # re-exported
from src.core.trace.trace_context import TraceContext


@runtime_checkable
class TraceCollector(Protocol):
    """Minimal entry-point dependency used by Tools and ingestion helpers.

    Plan §6.5: every caller receives the protocol, not a specific backend, so
    the JSONL legacy and the new SQLite store (plan §C2.2) are drop-in
    replacements of each other.
    """

    def collect(self, trace: TraceContext) -> None: ...


class JSONLTraceCollector:
    """JSON Lines backend kept for tests and roll-back compatibility.

    Plan §11.1 forbids new production writes here. The class is intentionally
    explicit so anyone reaching for it makes an informed choice.
    """

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


# Backward-compatibility alias: tools/tests that predate C2.2 still reference
# the legacy spelling.
JsonlTraceCollector = JSONLTraceCollector


__all__ = [
    "JSONLTraceCollector",
    "JsonlTraceCollector",
    "SQLiteTraceStore",
    "TraceCollector",
]
