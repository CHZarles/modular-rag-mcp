"""应用 stderr 日志与 Trace JSON Lines 持久化。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.types import JsonDict

_DEFAULT_TRACES_PATH = Path("logs/traces.jsonl")
_TRACE_LOGGER_NAME = "modular-rag.trace"
_TRACE_HANDLER_MARKER = "_modular_rag_trace_handler"
_STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}


def get_logger(name: str) -> logging.Logger:
    """返回向标准错误流输出的 Logger。"""
    logger = logging.getLogger(name)
    # 多次启动或重复获取同名 Logger 时，避免叠加重复的 stderr Handler。
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
        logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


class JSONFormatter(logging.Formatter):
    """把一条 LogRecord 格式化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: JsonDict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRIBUTES or key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)

        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_trace_logger(
    traces_path: str | Path = _DEFAULT_TRACES_PATH,
    *,
    name: str = _TRACE_LOGGER_NAME,
) -> logging.Logger:
    """返回向指定文件追加单行 JSON 的独立 Trace Logger。"""
    path = Path(traces_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path.resolve())

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.disabled = False

    matching_handler: logging.FileHandler | None = None
    for handler in list(logger.handlers):
        if not getattr(handler, _TRACE_HANDLER_MARKER, False):
            continue
        if (
            matching_handler is None
            and isinstance(handler, logging.FileHandler)
            and handler.baseFilename == target
        ):
            matching_handler = handler
            continue
        logger.removeHandler(handler)
        handler.close()

    if matching_handler is None:
        matching_handler = logging.FileHandler(path, encoding="utf-8")
        matching_handler.setFormatter(JSONFormatter())
        setattr(matching_handler, _TRACE_HANDLER_MARKER, True)
        logger.addHandler(matching_handler)
    return logger


def write_trace(
    trace_dict: JsonDict,
    traces_path: str | Path = _DEFAULT_TRACES_PATH,
) -> None:
    """把 Trace 字典作为一个顶层 JSON 对象追加到 JSON Lines 文件。"""
    path = Path(traces_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace_dict, ensure_ascii=False) + "\n")
