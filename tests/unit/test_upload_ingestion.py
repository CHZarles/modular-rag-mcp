"""Concurrency and validation tests for the shared upload coordinator."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from src.application.ingestion_jobs import (
    IngestionJobService,
    IngestionQueueFullError,
)
from src.application.upload_ingestion import (
    UploadIngestionCoordinator,
    UploadRejectedError,
)
from src.core.settings import Settings
from src.core.types import IngestionRequest, IngestionResult


class BlockingIngestion:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.contents: list[bytes] = []

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Any = None,
        trace: Any = None,
    ) -> IngestionResult:
        del trace
        self.contents.append(Path(request.source_path).read_bytes())
        self.started.set()
        if on_progress is not None:
            on_progress("load", 2, 7)
        if not self.release.wait(timeout=3):
            raise TimeoutError("test worker was not released")
        return IngestionResult(
            source_path=request.source_path,
            collection=request.collection,
            status="success",
            file_hash="hash",
            document_id="doc-1",
        )


def test_same_target_is_rejected_before_it_can_overwrite_active_source(
    tmp_path: Path,
) -> None:
    ingestion = BlockingIngestion()
    jobs = IngestionJobService(max_workers=1)
    coordinator = _coordinator(tmp_path, ingestion, jobs)
    first = b"%PDF-1.4\nfirst"
    second = b"%PDF-1.4\nsecond"
    try:
        job = coordinator.submit(filename="guide.pdf", content=first, collection="docs")
        assert ingestion.started.wait(timeout=1)

        with pytest.raises(UploadRejectedError) as caught:
            coordinator.submit(filename="guide.pdf", content=second, collection="docs")

        assert caught.value.code == "document_busy"
        assert (tmp_path / "uploads" / "guide.pdf").read_bytes() == first
        assert not list((tmp_path / "uploads").glob("*.uploading"))
        ingestion.release.set()
        assert _wait_for(jobs, job.job_id, "success").result is not None
    finally:
        ingestion.release.set()
        coordinator.shutdown()


def test_different_uploads_queue_and_keep_their_own_bytes(tmp_path: Path) -> None:
    ingestion = BlockingIngestion()
    jobs = IngestionJobService(max_workers=1, max_active_jobs=2)
    coordinator = _coordinator(tmp_path, ingestion, jobs)
    try:
        first = coordinator.submit(filename="a.pdf", content=b"%PDF-1.4\na")
        assert ingestion.started.wait(timeout=1)
        second = coordinator.submit(filename="b.pdf", content=b"%PDF-1.4\nb")

        assert jobs.get(first.job_id).status == "running"
        assert jobs.get(second.job_id).status == "queued"
        ingestion.release.set()
        _wait_for(jobs, first.job_id, "success")
        _wait_for(jobs, second.job_id, "success")
        assert ingestion.contents == [b"%PDF-1.4\na", b"%PDF-1.4\nb"]
    finally:
        ingestion.release.set()
        coordinator.shutdown()


def test_full_queue_cleans_staging_and_releases_target_lock(tmp_path: Path) -> None:
    ingestion = BlockingIngestion()
    jobs = IngestionJobService(max_workers=1, max_active_jobs=1)
    coordinator = _coordinator(tmp_path, ingestion, jobs)
    try:
        first = coordinator.submit(filename="a.pdf", content=b"%PDF-1.4\na")
        assert ingestion.started.wait(timeout=1)

        with pytest.raises(IngestionQueueFullError):
            coordinator.submit(filename="b.pdf", content=b"%PDF-1.4\nb")
        assert not list((tmp_path / "uploads").glob("*.uploading"))

        ingestion.release.set()
        _wait_for(jobs, first.job_id, "success")
        retry = coordinator.submit(filename="b.pdf", content=b"%PDF-1.4\nb")
        assert _wait_for(jobs, retry.job_id, "success").status == "success"
    finally:
        ingestion.release.set()
        coordinator.shutdown()


def test_target_lock_rejects_same_document_from_another_process(tmp_path: Path) -> None:
    upload_root = (tmp_path / "uploads").resolve()
    target = upload_root / "guide.pdf"
    lock_root = upload_root / ".locks"
    lock_root.mkdir(parents=True)
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{digest}.lock"
    script = """
import fcntl
import pathlib
import sys

with pathlib.Path(sys.argv[1]).open("a+b") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    ingestion = BlockingIngestion()
    coordinator = UploadIngestionCoordinator(
        _settings(),
        upload_root,
        lambda _settings: ingestion,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(UploadRejectedError) as caught:
            coordinator.submit(filename="guide.pdf", content=b"%PDF-1.4\ndemo")
        assert caught.value.code == "document_busy"
    finally:
        coordinator.shutdown()
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=3)


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("guide.txt", b"%PDF-1.4", "invalid_pdf"),
        ("guide.pdf", b"", "invalid_pdf"),
        ("guide.pdf", b"not a pdf", "invalid_pdf"),
        ("guide.pdf", b"%PDF-1.4\ntoo large", "file_too_large"),
    ],
)
def test_upload_boundary_rejects_invalid_files(
    tmp_path: Path,
    filename: str,
    content: bytes,
    code: str,
) -> None:
    ingestion = BlockingIngestion()
    coordinator = UploadIngestionCoordinator(
        _settings(),
        tmp_path / "uploads",
        lambda _settings: ingestion,
        max_upload_bytes=10,
    )
    try:
        with pytest.raises(UploadRejectedError) as caught:
            coordinator.submit(filename=filename, content=content)
        assert caught.value.code == code
    finally:
        coordinator.shutdown()


def _coordinator(
    tmp_path: Path,
    ingestion: BlockingIngestion,
    jobs: IngestionJobService,
) -> UploadIngestionCoordinator:
    return UploadIngestionCoordinator(
        _settings(),
        tmp_path / "uploads",
        lambda _settings: ingestion,
        jobs=jobs,
    )


def _wait_for(
    jobs: IngestionJobService,
    job_id: str,
    status: str,
):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job.status == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {status}: {jobs.get(job_id)}")


def _settings() -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={},
    )
