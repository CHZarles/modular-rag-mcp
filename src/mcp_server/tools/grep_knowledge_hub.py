"""MCP Tool for independent literal lookup over final Chunk text."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from src.core.services.grep_service import GrepResponse, GrepService
from src.core.trace import TraceCollector, TraceContext
from src.core.types import JsonDict
from src.ingestion.storage.sqlite_grep_index import (
    GrepIndexUnavailableError,
    normalize_search_text,
)
from src.mcp_server.tools.base import (
    DOCUMENT_TYPES,
    MAX_QUERY_CHARS,
    ToolArgumentError,
    ToolExecutionError,
    public_citation_metadata,
    public_source_label,
    sanitize_wire_string,
    validate_file_type,
    validate_identifier,
    validate_top_k,
)
from src.observability.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_ARGUMENTS = frozenset(
    {"pattern", "collection", "top_k", "case_sensitive", "file_type"}
)


class GrepKnowledgeHubTool:
    name = "grep_knowledge_hub"
    description = "按字面量精确查找本地知识库中的完整文本片段"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 3,
                "maxLength": MAX_QUERY_CHARS,
                "description": "要原样查找的字面量",
            },
            "collection": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "default": "default",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 20,
            },
            "case_sensitive": {"type": "boolean", "default": False},
            "file_type": {
                "type": "string",
                "enum": list(DOCUMENT_TYPES),
                "description": "按源文件类型筛选结果",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        get_service: Callable[[], GrepService],
        *,
        get_collector: Callable[[], TraceCollector | None] | None = None,
    ) -> None:
        self.get_service = get_service
        self.get_collector = get_collector

    def call(self, arguments: JsonDict) -> JsonDict:
        unknown = sorted(set(arguments) - _ALLOWED_ARGUMENTS)
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")
        case_sensitive = arguments.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise ToolArgumentError("case_sensitive must be a boolean")
        pattern = _validate_pattern(arguments.get("pattern"), case_sensitive=case_sensitive)
        collection = validate_identifier(
            arguments.get("collection", "default"), field="collection"
        )
        top_k = validate_top_k(arguments.get("top_k"), default=20)
        file_type = validate_file_type(arguments.get("file_type"))
        collector = self.get_collector() if self.get_collector is not None else None
        trace = TraceContext(trace_type="query")
        started = time.monotonic()
        try:
            response = self.get_service().search(
                pattern=pattern,
                collection=collection,
                top_k=top_k,
                case_sensitive=case_sensitive,
                file_type=file_type,
            )
        except GrepIndexUnavailableError:
            _finish_trace(
                trace,
                collector,
                pattern=pattern,
                collection=collection,
                top_k=top_k,
                case_sensitive=case_sensitive,
                result_count=0,
                truncated=False,
                timed_out=False,
                status="failed",
                error_code="grep_unavailable",
                started=started,
            )
            raise ToolExecutionError("grep_unavailable") from None
        except Exception:
            logger.exception("grep_knowledge_hub execution failed")
            _finish_trace(
                trace,
                collector,
                pattern=pattern,
                collection=collection,
                top_k=top_k,
                case_sensitive=case_sensitive,
                result_count=0,
                truncated=False,
                timed_out=False,
                status="failed",
                error_code="grep_failed",
                started=started,
            )
            raise ToolExecutionError("grep_failed") from None

        _finish_trace(
            trace,
            collector,
            pattern=pattern,
            collection=collection,
            top_k=top_k,
            case_sensitive=case_sensitive,
            result_count=len(response.matches),
            truncated=response.truncated,
            timed_out=response.timed_out,
            status="success",
            error_code=None,
            started=started,
        )
        return _mcp_result(replace(response, trace_id=trace.trace_id))


def _validate_pattern(value: object, *, case_sensitive: bool) -> str:
    if not isinstance(value, str):
        raise ToolArgumentError("pattern must be a string")
    if "\0" in value:
        raise ToolArgumentError("pattern must not contain NUL")
    if len(value) < 3:
        raise ToolArgumentError("pattern must contain at least 3 Unicode characters")
    if len(value) > MAX_QUERY_CHARS:
        raise ToolArgumentError(f"pattern must not exceed {MAX_QUERY_CHARS} characters")
    effective = value if case_sensitive else normalize_search_text(value)
    if len(effective) < 3:
        raise ToolArgumentError("normalized pattern must contain at least 3 Unicode characters")
    return value


def _mcp_result(response: GrepResponse) -> JsonDict:
    matches = [
        {
            "chunk_id": sanitize_wire_string(match.chunk_id),
            "text": sanitize_wire_string(match.text),
            "source": public_source_label(match.source_path),
            "page": match.page,
            "metadata": public_citation_metadata(match.metadata),
            "match_count": match.match_count,
        }
        for match in response.matches
    ]
    return {
        "content": [{"type": "text", "text": _markdown(matches)}],
        "structuredContent": {
            "matches": matches,
            "truncated": response.truncated,
            "timed_out": response.timed_out,
            "trace_id": response.trace_id,
        },
    }


def _markdown(matches: list[JsonDict]) -> str:
    if not matches:
        return "未找到精确匹配。"
    lines = ["精确查找结果："]
    for index, match in enumerate(matches, start=1):
        page = f"，第 {match['page']} 页" if match.get("page") is not None else ""
        lines.extend(
            [
                "",
                f"[{index}] {match['source']}{page}，命中 {match['match_count']} 次",
                str(match["text"]),
            ]
        )
    return "\n".join(lines)


def _finish_trace(
    trace: TraceContext,
    collector: TraceCollector | None,
    *,
    pattern: str,
    collection: str,
    top_k: int,
    case_sensitive: bool,
    result_count: int,
    truncated: bool,
    timed_out: bool,
    status: str,
    error_code: str | None,
    started: float,
) -> None:
    trace.metadata = {
        "retrieval_mode": "grep",
        "pattern_length": len(pattern),
        "collection": collection,
        "top_k": top_k,
        "case_sensitive": case_sensitive,
        "result_count": result_count,
        "truncated": truncated,
        "timed_out": timed_out,
        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 2),
        "status": status,
        "error_code": error_code,
    }
    if collector is not None:
        try:
            collector.collect(trace)
        except Exception:
            logger.warning("trace collector rejected a grep entry", exc_info=True)


__all__ = ["GrepKnowledgeHubTool"]
