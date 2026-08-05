"""Stderr JSON formatter tests.

The Trace JSON Lines writer (``get_trace_logger`` / ``write_trace``) was
removed in plan §C2.4 because production no longer persists traces to a
JSONL file; the SQLite store is the read-write source of truth. The
stderr formatter below is the only piece kept — every other test moved
with its producer.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.observability.logger import JSONFormatter


def _record(message: str = "trace recorded", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.trace",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_single_line_with_standard_and_extra_fields() -> None:
    formatter = JSONFormatter()

    line = formatter.format(
        _record("line one\nline two", trace_type="query", custom=Path("trace.jsonl"))
    )
    payload = json.loads(line)

    assert "\n" not in line
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.trace"
    assert payload["message"] == "line one\nline two"
    assert payload["trace_type"] == "query"
    assert payload["custom"] == "trace.jsonl"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_includes_exception_text() -> None:
    formatter = JSONFormatter()
    try:
        raise ValueError("invalid trace")
    except ValueError:
        record = logging.LogRecord(
            name="test.trace",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="write failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "write failed"
    assert "ValueError: invalid trace" in payload["exception"]
