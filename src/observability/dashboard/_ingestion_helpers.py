"""Shared ingestion helpers used by the Dashboard API and other internal callers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from src.core.settings import Settings


class UploadedPdf(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


def store_uploaded_pdf(uploaded: UploadedPdf, upload_root: Path) -> Path:
    filename = Path(uploaded.name).name
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("只支持 PDF 文件")
    content = uploaded.getvalue()
    if not content:
        raise ValueError("PDF 文件不能为空")
    upload_root.mkdir(parents=True, exist_ok=True)
    path = (upload_root / filename).resolve()
    with NamedTemporaryFile(
        dir=upload_root,
        prefix=f".{filename}.",
        suffix=".uploading",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def settings_for_ingestion_profile(
    settings: Settings,
    *,
    ai_enrichment: bool,
) -> Settings:
    if ai_enrichment:
        return settings
    ingestion = dict(settings.ingestion)
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
        "enabled": False,
    }
    return replace(settings, ingestion=ingestion)


def collection_options(settings: Settings, indexed_collections: list[str]) -> list[str]:
    names = {"default", *indexed_collections}
    configured = settings.vector_store.get("collection_name")
    if isinstance(configured, str) and configured.strip():
        names.add(configured.strip())
    dashboard_collections = settings.dashboard.get("collections")
    if isinstance(dashboard_collections, list):
        names.update(str(item).strip() for item in dashboard_collections if str(item).strip())
    return sorted(names, key=str.casefold)


def dashboard_ai_enrichment_default(settings: Settings) -> bool:
    value = settings.dashboard.get("ingestion_ai_enrichment_default", False)
    if not isinstance(value, bool):
        raise ValueError(
            "dashboard configuration error: ingestion_ai_enrichment_default must be boolean"
        )
    return value


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "UploadedPdf",
    "collection_options",
    "dashboard_ai_enrichment_default",
    "settings_for_ingestion_profile",
    "store_uploaded_pdf",
]
