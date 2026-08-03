from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import Settings
from observability.dashboard._ingestion_helpers import (
    collection_options,
    settings_for_ingestion_profile,
    store_uploaded_pdf,
)


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self.content = content

    def getvalue(self) -> bytes:
        return self.content


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
        store_uploaded_pdf(upload, tmp_path)


def test_store_uploaded_pdf_uses_safe_stable_filename(tmp_path: Path) -> None:
    path = store_uploaded_pdf(FakeUpload("../guide.pdf", b"pdf-content"), tmp_path)

    assert path == (tmp_path / "guide.pdf").resolve()
    assert path.read_bytes() == b"pdf-content"


def test_fast_profile_disables_model_enrichment_without_mutating_settings() -> None:
    settings = _settings()

    fast = settings_for_ingestion_profile(settings, ai_enrichment=False)

    assert fast.ingestion["chunk_refiner"]["use_llm"] is False
    assert fast.ingestion["metadata_enricher"]["use_llm"] is False
    assert fast.ingestion["image_captioner"]["enabled"] is False
    assert settings.ingestion["chunk_refiner"]["use_llm"] is True
    assert settings_for_ingestion_profile(settings, ai_enrichment=True) is settings


def test_collection_options_include_configured_dashboard_names() -> None:
    settings = _settings()
    settings.vector_store["collection_name"] = "backend-default"
    settings.dashboard["collections"] = ["notes"]

    assert collection_options(settings, ["papers"]) == [
        "backend-default",
        "default",
        "notes",
        "papers",
    ]


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
        observability={"enabled": True},
        ingestion={
            "chunk_refiner": {"use_llm": True, "keep": "refiner"},
            "metadata_enricher": {"use_llm": True, "keep": "metadata"},
            "image_captioner": {"enabled": True, "keep": "image"},
        },
    )
