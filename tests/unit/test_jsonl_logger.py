"""结构化 JSON Lines 日志与 Trace 持久化测试。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.core.trace import TraceContext
from src.observability.logger import JSONFormatter, get_trace_logger, write_trace


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


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


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


def test_trace_logger_writes_jsonl_without_duplicate_handlers(tmp_path: Path) -> None:
    traces_path = tmp_path / "nested" / "traces.jsonl"
    logger_name = f"test.trace.{tmp_path.name}"
    logger = get_trace_logger(traces_path, name=logger_name)
    try:
        same_logger = get_trace_logger(traces_path, name=logger_name)
        logger.info("query complete", extra={"trace_type": "query", "elapsed_ms": 3.5})

        lines = traces_path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        assert same_logger is logger
        assert len(logger.handlers) == 1
        assert len(lines) == 1
        assert payload["trace_type"] == "query"
        assert payload["elapsed_ms"] == 3.5
    finally:
        _close_handlers(logger)


def test_trace_logger_retargets_owned_handler_when_path_changes(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    logger_name = f"test.trace.retarget.{tmp_path.name}"
    logger = get_trace_logger(first_path, name=logger_name)
    try:
        logger.info("first")
        retargeted = get_trace_logger(second_path, name=logger_name)
        retargeted.info("second")

        assert [json.loads(line)["message"] for line in first_path.read_text().splitlines()] == [
            "first"
        ]
        assert [json.loads(line)["message"] for line in second_path.read_text().splitlines()] == [
            "second"
        ]
        assert len(logger.handlers) == 1
    finally:
        _close_handlers(logger)


def test_write_trace_appends_trace_context_as_top_level_json(tmp_path: Path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    query_trace = TraceContext(trace_type="query")
    query_trace.finish()
    ingestion_trace = TraceContext(trace_type="ingestion")
    ingestion_trace.record_stage("load", {"document_count": 1}, elapsed_ms=2.0)
    ingestion_trace.finish()

    write_trace(query_trace.to_dict(), traces_path)
    write_trace(ingestion_trace.to_dict(), traces_path)

    payloads = [
        json.loads(line) for line in traces_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [payload["trace_type"] for payload in payloads] == ["query", "ingestion"]
    assert payloads[1]["stages"][0]["stage"] == "load"
