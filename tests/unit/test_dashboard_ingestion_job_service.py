from __future__ import annotations

import time
from threading import Event
from typing import Any

import pytest

from core.types import IngestionRequest, IngestionResult
from observability.dashboard.services import IngestionJobService


class BlockingIngestion:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.request: IngestionRequest | None = None

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Any = None,
        trace: Any = None,
    ) -> IngestionResult:
        del trace
        self.request = request
        self.started.set()
        if on_progress is not None:
            on_progress("transform", 4, 7)
        if not self.release.wait(timeout=2):
            raise TimeoutError("test worker was not released")
        return IngestionResult(
            source_path=request.source_path,
            collection=request.collection,
            status="success",
            file_hash="revision",
            chunk_count=3,
        )


def test_job_service_returns_immediately_and_exposes_live_progress() -> None:
    jobs = IngestionJobService()
    ingestion = BlockingIngestion()
    request = IngestionRequest(source_path="/tmp/guide.pdf", collection="docs")
    try:
        submitted = jobs.submit(ingestion, request)  # type: ignore[arg-type]

        assert ingestion.started.wait(timeout=1)
        running = _wait_for(jobs, submitted.job_id, "running")
        assert running.active is True
        assert running.stage == "transform"
        assert (running.step, running.total) == (4, 7)
        assert jobs.latest_active() == running
        assert ingestion.request is not None
        assert ingestion.request.request_id == submitted.job_id

        with pytest.raises(ValueError, match="already running"):
            jobs.submit(ingestion, request)  # type: ignore[arg-type]

        ingestion.release.set()
        completed = _wait_for(jobs, submitted.job_id, "success")
        assert completed.active is False
        assert completed.result is not None
        assert completed.result.chunk_count == 3
        assert jobs.latest_active() is None
    finally:
        ingestion.release.set()
        jobs.shutdown()


def test_job_service_records_unhandled_worker_failure() -> None:
    class FailingIngestion:
        def ingest(
            self, request: IngestionRequest, on_progress: Any = None, trace: Any = None
        ) -> Any:
            raise RuntimeError("provider unavailable")

    jobs = IngestionJobService()
    try:
        job = jobs.submit(
            FailingIngestion(),  # type: ignore[arg-type]
            IngestionRequest(source_path="/tmp/failing.pdf"),
        )

        failed = _wait_for(jobs, job.job_id, "failed")
        assert failed.error == "provider unavailable"
    finally:
        jobs.shutdown()


def test_job_service_bounds_terminal_history() -> None:
    class ImmediateIngestion:
        def ingest(
            self, request: IngestionRequest, on_progress: Any = None, trace: Any = None
        ) -> IngestionResult:
            return IngestionResult(
                source_path=request.source_path,
                collection=request.collection,
                status="success",
                file_hash="hash",
            )

    jobs = IngestionJobService(history_limit=1)
    try:
        first = jobs.submit(
            ImmediateIngestion(),  # type: ignore[arg-type]
            IngestionRequest(source_path="/tmp/first.pdf"),
        )
        _wait_for(jobs, first.job_id, "success")
        second = jobs.submit(
            ImmediateIngestion(),  # type: ignore[arg-type]
            IngestionRequest(source_path="/tmp/second.pdf"),
        )
        _wait_for(jobs, second.job_id, "success")

        with pytest.raises(KeyError, match="ingestion job not found"):
            jobs.get(first.job_id)
        assert jobs.get(second.job_id).status == "success"
    finally:
        jobs.shutdown()


def _wait_for(
    jobs: IngestionJobService,
    job_id: str,
    expected_status: str,
):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job.status == expected_status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected_status}: {jobs.get(job_id)}")
