"""Safe upload staging and bounded background ingestion orchestration."""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from src.application.ingestion_jobs import IngestionJob, IngestionJobService
from src.application.services import IngestionService
from src.core.settings import Settings
from src.core.trace import TraceCollector, TraceContext
from src.core.types import IngestionRequest, IngestionResult, ProgressCallback
from src.libs.loader.csv_loader import csv_to_markdown
from src.libs.loader.docx_loader import validate_docx
from src.libs.loader.format_router import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS
from src.libs.loader.image_loader import validate_image

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILENAME_CHARS = 255
MAX_COLLECTION_CHARS = 128


class UploadRejectedError(ValueError):
    """Expected upload rejection with a stable public error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UploadIngestionCoordinator:
    """Stage one immutable upload and hand it to the shared ingestion queue."""

    def __init__(
        self,
        settings: Settings,
        upload_root: str | Path,
        ingestion_factory: Callable[[Settings], IngestionService],
        *,
        jobs: IngestionJobService | None = None,
        collector: TraceCollector | None = None,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
    ) -> None:
        if (
            isinstance(max_upload_bytes, bool)
            or not isinstance(max_upload_bytes, int)
            or max_upload_bytes <= 0
        ):
            raise ValueError("max_upload_bytes must be positive")
        self.settings = settings
        self.upload_root = Path(upload_root).expanduser().resolve()
        self.ingestion_factory = ingestion_factory
        self.jobs = jobs or IngestionJobService(max_workers=1)
        self.collector = collector
        self.max_upload_bytes = max_upload_bytes

    def submit(
        self,
        *,
        filename: str,
        content: bytes,
        collection: str = "default",
        force: bool = False,
        ai_enrichment: bool = False,
        request_id: str | None = None,
    ) -> IngestionJob:
        safe_name = _safe_filename(filename)
        normalized_collection = _collection(collection)
        extension = Path(safe_name).suffix.lower()
        _validate_upload(extension, content, self.max_upload_bytes)
        if not isinstance(force, bool) or not isinstance(ai_enrichment, bool):
            raise UploadRejectedError("invalid_upload_options")

        self.upload_root.mkdir(parents=True, exist_ok=True)
        target = self.upload_root / safe_name
        lock = _TargetLock.acquire(target)
        staged: Path | None = None
        try:
            staged = _stage(content, self.upload_root, safe_name)
            ingestion = _StagedUploadIngestion(
                staged=staged,
                target=target,
                lock=lock,
                settings=settings_for_ingestion_profile(
                    self.settings,
                    ai_enrichment=ai_enrichment,
                    require_image_caption=extension in IMAGE_EXTENSIONS,
                ),
                ingestion_factory=self.ingestion_factory,
            )
            return self.jobs.submit(
                ingestion,
                IngestionRequest(
                    source_path=str(target),
                    collection=normalized_collection,
                    force=force,
                    request_id=request_id,
                ),
                self.collector,
            )
        except Exception:
            if staged is not None:
                staged.unlink(missing_ok=True)
            lock.release()
            raise

    def get(self, job_id: str) -> IngestionJob:
        return self.jobs.get(job_id)

    def latest_active(self) -> IngestionJob | None:
        return self.jobs.latest_active()

    def shutdown(self, *, wait: bool = True) -> None:
        self.jobs.shutdown(wait=wait)


class _StagedUploadIngestion:
    def __init__(
        self,
        *,
        staged: Path,
        target: Path,
        lock: _TargetLock,
        settings: Settings,
        ingestion_factory: Callable[[Settings], IngestionService],
    ) -> None:
        self.staged = staged
        self.target = target
        self.lock = lock
        self.settings = settings
        self.ingestion_factory = ingestion_factory

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: ProgressCallback | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        try:
            self.staged.replace(self.target)
            return self.ingestion_factory(self.settings).ingest(
                request,
                on_progress=on_progress,
                trace=trace,
            )
        finally:
            self.staged.unlink(missing_ok=True)
            self.lock.release()


class _TargetLock:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream: BinaryIO | None = stream

    @classmethod
    def acquire(cls, target: Path) -> _TargetLock:
        lock_root = target.parent / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
        stream = (lock_root / f"{digest}.lock").open("a+b")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            stream.close()
            raise UploadRejectedError("document_busy") from exc
        return cls(stream)

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def settings_for_ingestion_profile(
    settings: Settings,
    *,
    ai_enrichment: bool,
    require_image_caption: bool = False,
) -> Settings:
    if ai_enrichment and not require_image_caption:
        return settings
    ingestion = dict(settings.ingestion)
    if not ai_enrichment:
        ingestion["chunk_refiner"] = {
            **_mapping(ingestion.get("chunk_refiner")),
            "use_llm": False,
        }
        ingestion["metadata_enricher"] = {
            **_mapping(ingestion.get("metadata_enricher")),
            "use_llm": False,
        }
    ingestion["image_captioner"] = {
        **_mapping(ingestion.get("image_captioner")),
        "enabled": require_image_caption,
    }
    return replace(settings, ingestion=ingestion)


def _safe_filename(value: object) -> str:
    if not isinstance(value, str):
        raise UploadRejectedError("invalid_filename")
    filename = Path(value.strip()).name
    if not filename or filename in {".", ".."} or len(filename) > MAX_FILENAME_CHARS:
        raise UploadRejectedError("invalid_filename")
    if any(ord(character) < 32 for character in filename):
        raise UploadRejectedError("invalid_filename")
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UploadRejectedError("unsupported_file_type")
    return filename


def _collection(value: object) -> str:
    if not isinstance(value, str):
        raise UploadRejectedError("invalid_collection")
    collection = value.strip()
    if not collection or len(collection) > MAX_COLLECTION_CHARS:
        raise UploadRejectedError("invalid_collection")
    return collection


def _validate_upload(extension: str, content: object, max_upload_bytes: int) -> None:
    error_code = {
        ".pdf": "invalid_pdf",
        ".docx": "invalid_docx",
        ".csv": "invalid_csv",
        ".png": "invalid_image",
        ".jpg": "invalid_image",
        ".jpeg": "invalid_image",
        ".webp": "invalid_image",
    }[extension]
    if not isinstance(content, bytes) or not content:
        raise UploadRejectedError(error_code)
    if len(content) > max_upload_bytes:
        raise UploadRejectedError("file_too_large")
    try:
        if extension == ".pdf":
            if b"%PDF-" not in content[:1024]:
                raise ValueError("invalid PDF header")
        elif extension == ".docx":
            validate_docx(content)
        elif extension == ".csv":
            csv_to_markdown(content, "upload")
        else:
            validate_image(content, extension)
    except ValueError as exc:
        raise UploadRejectedError(error_code) from exc


def _stage(content: bytes, upload_root: Path, filename: str) -> Path:
    with NamedTemporaryFile(
        dir=upload_root,
        prefix=f".{filename}.",
        suffix=".uploading",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UploadIngestionCoordinator",
    "UploadRejectedError",
    "settings_for_ingestion_profile",
]
