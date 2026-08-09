"""Bounded background ingestion jobs shared by every application entry point."""

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


class DocumentBusyError(ValueError):
    """The same logical document already has an active ingestion job."""


class IngestionQueueFullError(ValueError):
    """The bounded ingestion queue has no remaining capacity."""


@dataclass(frozen=True)
class IngestionJob:
    """Immutable job snapshot safe to read while a worker updates it."""

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
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE_STATUSES


class IngestionJobService:
    """Run bounded ingestion work outside request threads and expose snapshots."""

    def __init__(
        self,
        max_workers: int = 1,
        *,
        max_active_jobs: int = 8,
        history_limit: int = 100,
    ) -> None:
        for name, value in (
            ("max_workers", max_workers),
            ("max_active_jobs", max_active_jobs),
            ("history_limit", history_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"ingestion job service {name} must be a positive integer")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="rag-ingestion",
        )
        self._max_active_jobs = max_active_jobs
        self._history_limit = history_limit
        self._jobs: dict[str, IngestionJob] = {}
        self._lock = Lock()

    def submit(
        self,
        ingestion: IngestionService,
        request: IngestionRequest,
        collector: TraceCollector | None = None,
    ) -> IngestionJob:
        """Queue one request without allowing duplicate or unbounded active work."""
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
                raise DocumentBusyError(
                    f"document ingestion is already running: {duplicate.job_id}"
                )
            if sum(job.active for job in self._jobs.values()) >= self._max_active_jobs:
                raise IngestionQueueFullError("ingestion queue is full")
            self._jobs[job_id] = job

        queued_request = replace(
            request,
            source_path=normalized_source,
            collection=normalized_collection,
            request_id=request.request_id or job_id,
        )
        try:
            self._executor.submit(self._run, job_id, ingestion, queued_request, collector)
        except Exception:
            with self._lock:
                self._jobs.pop(job_id, None)
            raise
        return job

    def get(self, job_id: str) -> IngestionJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"ingestion job not found: {job_id}") from exc

    def latest_active(self) -> IngestionJob | None:
        with self._lock:
            return next(
                (job for job in reversed(self._jobs.values()) if job.active),
                None,
            )

    def shutdown(self, *, wait: bool = True) -> None:
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
            result = run_traced_ingestion(ingestion, request, collector, update_progress)
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                error=str(exc) or type(exc).__name__,
                finished_at=time(),
            )
            return

        terminal_status: JobStatus = result.status
        self._update(
            job_id,
            status=terminal_status,
            stage="complete" if terminal_status != "failed" else "failed",
            step=self.get(job_id).total,
            result=result,
            error=result.error,
            finished_at=time(),
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
        finished_at: float | None = None,
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
                finished_at=finished_at if finished_at is not None else current.finished_at,
            )
            self._prune_history_locked()

    def _prune_history_locked(self) -> None:
        terminal_ids = [job_id for job_id, job in self._jobs.items() if not job.active]
        for job_id in terminal_ids[: -self._history_limit]:
            del self._jobs[job_id]


__all__ = [
    "DocumentBusyError",
    "IngestionJob",
    "IngestionJobService",
    "IngestionQueueFullError",
    "JobStatus",
]
