"""应用 stderr 结构化日志。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.core.types import JsonDict

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


__all__ = ["JSONFormatter", "get_logger"]
