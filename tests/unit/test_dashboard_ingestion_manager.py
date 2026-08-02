from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.types import IngestionRequest, IngestionResult
from observability.dashboard.pages.ingestion_manager import (
    _ingest_uploaded_pdf,
    _store_uploaded_pdf,
)


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self.content = content

    def getvalue(self) -> bytes:
        return self.content


class FakeIngestionService:
    def __init__(self) -> None:
        self.request: IngestionRequest | None = None
        self.content = b""

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Any = None,
        trace: Any = None,
    ) -> IngestionResult:
        del trace
        self.request = request
        self.content = Path(request.source_path).read_bytes()
        if on_progress is not None:
            on_progress("integrity", 1, 7)
        return IngestionResult(
            source_path=request.source_path,
            collection=request.collection,
            status="success",
            file_hash="revision",
            chunk_count=3,
        )


def test_ingest_uploaded_pdf_persists_stable_source_and_forwards_progress(
    tmp_path: Path,
) -> None:
    service = FakeIngestionService()
    progress: list[tuple[str, int, int]] = []

    result = _ingest_uploaded_pdf(
        FakeUpload("../guide.pdf", b"pdf-content"),
        "docs",
        True,
        service,  # type: ignore[arg-type]
        lambda stage, step, total: progress.append((stage, step, total)),
        tmp_path / "uploads",
        None,
    )

    assert result.status == "success"
    assert service.request is not None
    assert service.request.collection == "docs"
    assert service.request.force is True
    assert Path(service.request.source_path) == (tmp_path / "uploads" / "guide.pdf").resolve()
    assert Path(service.request.source_path).is_file()
    assert service.content == b"pdf-content"
    assert progress == [("integrity", 1, 7)]


@pytest.mark.parametrize(
    ("upload", "message"),
    [
        (FakeUpload("guide.txt", b"content"), "只支持 PDF"),
        (FakeUpload("guide.pdf", b""), "不能为空"),
    ],
)
def test_store_uploaded_pdf_rejects_invalid_files(
    tmp_path: Path,
    upload: FakeUpload,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _store_uploaded_pdf(upload, tmp_path)
