"""MCP upload and ingestion-job Tool contract tests."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from src.application.ingestion_jobs import IngestionJob
from src.application.upload_ingestion import UploadRejectedError
from src.core.types import IngestionResult
from src.mcp_server.tools import GetIngestionJobTool, UploadDocumentTool


class FakeCoordinator:
    def __init__(self, job: IngestionJob) -> None:
        self.job = job
        self.submissions: list[dict[str, Any]] = []

    def submit(self, **arguments: Any) -> IngestionJob:
        self.submissions.append(arguments)
        return self.job

    def get(self, job_id: str) -> IngestionJob:
        if job_id != self.job.job_id:
            raise KeyError(job_id)
        return self.job


def test_upload_document_decodes_pdf_and_returns_public_job() -> None:
    job = IngestionJob("job-1", "/private/uploads/guide.pdf", "docs")
    coordinator = FakeCoordinator(job)
    content = b"%PDF-1.4\ndemo"

    result = UploadDocumentTool(lambda: coordinator).call(  # type: ignore[arg-type]
        {
            "filename": "../guide.pdf",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "collection": "docs",
            "force": True,
            "ai_enrichment": False,
        }
    )

    assert "isError" not in result
    assert result["structuredContent"]["job"] == {
        "job_id": "job-1",
        "filename": "guide.pdf",
        "collection": "docs",
        "status": "queued",
        "stage": "queued",
        "step": 0,
        "total": 7,
        "active": True,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    assert coordinator.submissions == [
        {
            "filename": "../guide.pdf",
            "content": content,
            "collection": "docs",
            "force": True,
            "ai_enrichment": False,
            "request_id": None,
        }
    ]


def test_upload_document_returns_stable_validation_and_busy_errors() -> None:
    coordinator = FakeCoordinator(IngestionJob("job-1", "/tmp/a.pdf", "docs"))
    tool = UploadDocumentTool(lambda: coordinator)  # type: ignore[arg-type]

    invalid = tool.call({"filename": "a.pdf", "content_base64": "not base64"})
    assert invalid["isError"] is True
    assert invalid["structuredContent"] == {"error": {"code": "invalid_pdf"}}

    def reject(**_arguments: Any) -> IngestionJob:
        raise UploadRejectedError("document_busy")

    coordinator.submit = reject  # type: ignore[method-assign]
    busy = tool.call(
        {
            "filename": "a.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
        }
    )
    assert busy["structuredContent"] == {"error": {"code": "document_busy"}}


def test_get_ingestion_job_redacts_paths_and_internal_failure() -> None:
    result_value = IngestionResult(
        source_path="/private/uploads/guide.pdf",
        collection="docs",
        status="failed",
        file_hash="secret-hash",
        error="provider said API key sk-secret",
    )
    job = IngestionJob(
        "job-1",
        "/private/uploads/guide.pdf",
        "docs",
        status="failed",
        stage="failed",
        result=result_value,
        error=result_value.error,
    )
    coordinator = FakeCoordinator(job)

    result = GetIngestionJobTool(lambda: coordinator).call(  # type: ignore[arg-type]
        {"job_id": "job-1"}
    )

    payload = result["structuredContent"]["job"]
    assert payload["filename"] == Path(job.source_path).name
    assert payload["error"] == {"code": "ingestion_failed"}
    assert "/private" not in str(payload)
    assert "sk-secret" not in str(payload)
    assert "secret-hash" not in str(payload)


def test_get_ingestion_job_returns_public_not_found_error() -> None:
    coordinator = FakeCoordinator(IngestionJob("job-1", "/tmp/a.pdf", "docs"))

    result = GetIngestionJobTool(lambda: coordinator).call(  # type: ignore[arg-type]
        {"job_id": "missing"}
    )

    assert result["isError"] is True
    assert result["structuredContent"] == {
        "error": {"code": "job_not_found", "job_id": "missing"}
    }
