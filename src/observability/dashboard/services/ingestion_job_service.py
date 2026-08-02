"""Thread-safe background ingestion jobs for the Streamlit control plane."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Lock
from time import time
from typing import Literal
from uuid import uuid4

from src.application.services import IngestionService
from src.core.trace import TraceCollector
from src.core.types import IngestionRequest, IngestionResult
from src.observability.ingestion_trace import run_traced_ingestion

JobStatus = Literal["queued", "running", "success", "skipped", "failed"]
_ACTIVE_STATUSES = {"queued", "running"}


@dataclass(frozen=True)
class IngestionJob:
    """Immutable snapshot that can be rendered safely while a worker updates the job."""

    job_id: str
    source_path: str
    collection: str
    status: JobStatus = "queued"
    stage: str = "queued"
    step: int = 0
    total: int = 7
    result: IngestionResult | None = None
    error: str | None = None
    started_at: float | None = None

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE_STATUSES


class IngestionJobService:
    """Run ingestion outside Streamlit's request thread and expose polling snapshots."""

    def __init__(self, max_workers: int = 1) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("ingestion job service max_workers must be a positive integer")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="rag-ingestion",
        )
        self._jobs: dict[str, IngestionJob] = {}
        self._lock = Lock()

    def submit(
        self,
        ingestion: IngestionService,
        request: IngestionRequest,
        collector: TraceCollector | None = None,
    ) -> IngestionJob:
        """Queue one request and reject duplicate work for the same document boundary."""
        normalized_source = request.source_path.strip()
        normalized_collection = request.collection.strip()
        if not normalized_source or not normalized_collection:
            raise ValueError("ingestion job requires a source path and collection")

        job_id = uuid4().hex
        job = IngestionJob(
            job_id=job_id,
            source_path=normalized_source,
            collection=normalized_collection,
        )
        with self._lock:
            duplicate = next(
                (
                    existing
                    for existing in self._jobs.values()
                    if existing.active
                    and existing.source_path == normalized_source
                    and existing.collection == normalized_collection
                ),
                None,
            )
            if duplicate is not None:
                raise ValueError(f"document ingestion is already running: {duplicate.job_id}")
            self._jobs[job_id] = job

        queued_request = replace(
            request,
            source_path=normalized_source,
            collection=normalized_collection,
            request_id=request.request_id or job_id,
        )
        self._executor.submit(self._run, job_id, ingestion, queued_request, collector)
        return job

    def get(self, job_id: str) -> IngestionJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"ingestion job not found: {job_id}") from exc

    def latest_active(self) -> IngestionJob | None:
        """Recover the newest live job after a browser session is refreshed."""
        with self._lock:
            return next(
                (job for job in reversed(self._jobs.values()) if job.active),
                None,
            )

    def shutdown(self, *, wait: bool = True) -> None:
        """Release worker threads; intended for deterministic tests and process shutdown."""
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _run(
        self,
        job_id: str,
        ingestion: IngestionService,
        request: IngestionRequest,
        collector: TraceCollector | None,
    ) -> None:
        self._update(job_id, status="running", stage="integrity", started_at=time())

        def update_progress(stage: str, step: int, total: int) -> None:
            self._update(job_id, stage=stage, step=step, total=total)

        try:
            result = run_traced_ingestion(
                ingestion,
                request,
                collector,
                update_progress,
            )
        except Exception as exc:
            self._update(job_id, status="failed", error=str(exc) or type(exc).__name__)
            return

        terminal_status: JobStatus = result.status
        self._update(
            job_id,
            status=terminal_status,
            stage="complete" if terminal_status != "failed" else "failed",
            step=self.get(job_id).total,
            result=result,
            error=result.error,
        )

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: str | None = None,
        step: int | None = None,
        total: int | None = None,
        result: IngestionResult | None = None,
        error: str | None = None,
        started_at: float | None = None,
    ) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = replace(
                current,
                status=status if status is not None else current.status,
                stage=stage if stage is not None else current.stage,
                step=step if step is not None else current.step,
                total=total if total is not None else current.total,
                result=result if result is not None else current.result,
                error=error if error is not None else current.error,
                started_at=started_at if started_at is not None else current.started_at,
            )


__all__ = ["IngestionJob", "IngestionJobService", "JobStatus"]
