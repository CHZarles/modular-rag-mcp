"""MCP Tools for submitting supported uploads and polling ingestion jobs."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.application.ingestion_jobs import (
    DocumentBusyError,
    IngestionJob,
    IngestionQueueFullError,
)
from src.application.upload_ingestion import (
    MAX_UPLOAD_BYTES,
    UploadIngestionCoordinator,
    UploadRejectedError,
)
from src.core.types import JsonDict
from src.libs.loader.format_router import SUPPORTED_EXTENSIONS
from src.mcp_server.request_context import current_request_context
from src.mcp_server.tools.base import (
    ToolArgumentError,
    ToolExecutionError,
    validate_identifier,
)
from src.observability.logger import get_logger

logger = get_logger(__name__)

MAX_BASE64_CHARS = ((MAX_UPLOAD_BYTES + 2) // 3) * 4
_UPLOAD_ARGUMENTS = frozenset(
    {"filename", "content_base64", "collection", "force", "ai_enrichment"}
)
_JOB_ARGUMENTS = frozenset({"job_id"})


class UploadDocumentTool:
    name = "upload_document"
    description = "上传资料并提交后台摄取任务，立即返回可轮询的任务 ID"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "description": "原始文件名，支持 " + "、".join(SUPPORTED_EXTENSIONS),
            },
            "content_base64": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_BASE64_CHARS,
                "description": "文件的标准 Base64 内容",
            },
            "collection": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "default": "default",
            },
            "force": {"type": "boolean", "default": False},
            "ai_enrichment": {"type": "boolean", "default": False},
        },
        "required": ["filename", "content_base64"],
        "additionalProperties": False,
    }

    def __init__(self, get_coordinator: Callable[[], UploadIngestionCoordinator]) -> None:
        self.get_coordinator = get_coordinator

    def call(self, arguments: JsonDict) -> JsonDict:
        unknown = sorted(set(arguments) - _UPLOAD_ARGUMENTS)
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        filename = arguments.get("filename")
        encoded = arguments.get("content_base64")
        if not isinstance(filename, str) or not filename.strip():
            raise ToolArgumentError("filename must be a non-empty string")
        if not isinstance(encoded, str) or not encoded:
            raise ToolArgumentError("content_base64 must be a non-empty string")
        if len(encoded) > MAX_BASE64_CHARS:
            return _error_result("file_too_large")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return _error_result("invalid_file")

        collection = validate_identifier(
            arguments.get("collection", "default"), field="collection"
        )
        force = _boolean(arguments.get("force", False), "force")
        ai_enrichment = _boolean(
            arguments.get("ai_enrichment", False), "ai_enrichment"
        )
        try:
            request_context = current_request_context()
            job = self.get_coordinator().submit(
                filename=filename,
                content=content,
                collection=collection,
                force=force,
                ai_enrichment=ai_enrichment,
                request_id=(
                    request_context.request_id if request_context is not None else None
                ),
            )
        except UploadRejectedError as exc:
            return _error_result(exc.code)
        except DocumentBusyError:
            return _error_result("document_busy")
        except IngestionQueueFullError:
            return _error_result("ingestion_queue_full")
        except Exception:
            logger.exception("upload_document submission failed")
            raise ToolExecutionError("ingestion_submit_failed") from None
        return _job_result(job, message=f"摄取任务已排队：`{job.job_id}`。")


class GetIngestionJobTool:
    name = "get_ingestion_job"
    description = "按任务 ID 查询文件摄取状态、阶段进度和结果"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "upload_document 返回的任务 ID",
            }
        },
        "required": ["job_id"],
        "additionalProperties": False,
    }

    def __init__(self, get_coordinator: Callable[[], UploadIngestionCoordinator]) -> None:
        self.get_coordinator = get_coordinator

    def call(self, arguments: JsonDict) -> JsonDict:
        unknown = sorted(set(arguments) - _JOB_ARGUMENTS)
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")
        job_id = validate_identifier(arguments.get("job_id"), field="job_id")
        try:
            job = self.get_coordinator().get(job_id)
        except KeyError:
            return _error_result("job_not_found", job_id=job_id)
        except Exception:
            logger.exception("get_ingestion_job failed")
            raise ToolExecutionError("ingestion_job_lookup_failed") from None
        return _job_result(job, message=_job_message(job))


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ToolArgumentError(f"{field} must be a boolean")
    return value


def _job_result(job: IngestionJob, *, message: str) -> JsonDict:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"job": _public_job(job)},
    }


def _public_job(job: IngestionJob) -> JsonDict:
    payload: JsonDict = {
        "job_id": job.job_id,
        "filename": Path(job.source_path).name,
        "collection": job.collection,
        "status": job.status,
        "stage": job.stage,
        "step": job.step,
        "total": job.total,
        "active": job.active,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
    if job.result is not None:
        payload["result"] = {
            "status": job.result.status,
            "document_id": job.result.document_id,
            "chunk_count": job.result.chunk_count,
            "image_count": job.result.image_count,
        }
    else:
        payload["result"] = None
    payload["error"] = {"code": "ingestion_failed"} if job.status == "failed" else None
    return payload


def _job_message(job: IngestionJob) -> str:
    if job.status == "failed":
        return f"摄取任务 `{job.job_id}` 执行失败。"
    if job.active:
        return f"摄取任务 `{job.job_id}` 当前状态：{job.status}（{job.step}/{job.total}）。"
    return f"摄取任务 `{job.job_id}` 已完成，状态：{job.status}。"


def _error_result(code: str, **details: Any) -> JsonDict:
    error: JsonDict = {"code": code, **details}
    return {
        "content": [{"type": "text", "text": f"请求失败：{code}。"}],
        "structuredContent": {"error": error},
        "isError": True,
    }


__all__ = ["GetIngestionJobTool", "UploadDocumentTool"]
