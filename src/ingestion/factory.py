"""Configuration-driven assembly for the local ingestion pipeline."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from src.core.index_fingerprint import IndexFingerprintGuard
from src.core.settings import Settings
from src.ingestion.chunking import DocumentChunker
from src.ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from src.ingestion.pipeline import IngestionPipeline
from src.ingestion.storage import BM25Indexer, ImageStorage, SQLiteGrepIndex
from src.ingestion.transform import ChunkRefiner, ImageCaptioner, MetadataEnricher
from src.libs.loader import (
    CsvLoader,
    DocxLoader,
    FormatRouter,
    ImageLoader,
    MinerUPdfLoader,
    PdfLoader,
    SQLiteIntegrityStore,
)
from src.libs.vector_store import create_vector_store
from src.ports.ingestion import BaseLoader


def build_ingestion_pipeline(settings: Settings) -> IngestionPipeline:
    """Assemble one pipeline whose stores share the same generation control plane."""
    ingestion = settings.ingestion
    storage = ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")

    image_root = _required_text(storage, "image_root", "ingestion.storage")
    integrity = SQLiteIntegrityStore(
        _required_text(storage, "integrity_db_path", "ingestion.storage")
    )
    enable_dense = _optional_bool(
        settings.retrieval,
        "enable_dense",
        "retrieval",
        default=True,
    )
    vector_store = create_vector_store(settings)
    index_guard = _index_guard(settings, vector_store) if enable_dense else None
    bm25_store = BM25Indexer(
        _required_text(storage, "bm25_path", "ingestion.storage"),
        generation_store=integrity,
    )
    image_store = ImageStorage(
        _required_text(storage, "image_db_path", "ingestion.storage"),
        image_root,
        generation_store=integrity,
    )
    grep_index = None
    if settings.grep.get("enabled", False):
        grep_timeout_ms = settings.grep.get("timeout_ms", 1000)
        if not isinstance(grep_timeout_ms, int) or isinstance(grep_timeout_ms, bool):
            raise ValueError("Setting grep.timeout_ms must be a positive integer")
        grep_index = SQLiteGrepIndex(
            _required_text(settings.grep, "db_path", "grep"),
            timeout_ms=grep_timeout_ms,
        )

    return IngestionPipeline(
        integrity=integrity,
        loader=_create_format_router(ingestion, image_root),
        chunker=DocumentChunker(settings),
        transforms=[
            ChunkRefiner(settings),
            MetadataEnricher(settings),
            ImageCaptioner(settings),
        ],
        batch_processor=BatchProcessor(
            DenseEncoder(settings) if enable_dense else None,
            SparseEncoder(),
            batch_size=_positive_int(ingestion, "batch_size", "ingestion"),
        ),
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        grep_index=grep_index,
        enable_dense=enable_dense,
        claim_lease_seconds=_positive_float(
            ingestion,
            "claim_lease_seconds",
            "ingestion",
        ),
        index_dimension_validator=(
            index_guard.ensure_dimension if index_guard is not None else None
        ),
    )


def _create_format_router(ingestion: Mapping[str, Any], image_root: str) -> FormatRouter:
    pdf_loader = _create_loader(ingestion, image_root)
    image_loader = ImageLoader(image_root)
    return FormatRouter(
        {
            ".pdf": pdf_loader,
            ".docx": DocxLoader(image_root=image_root),
            ".csv": CsvLoader(),
            ".png": image_loader,
            ".jpg": image_loader,
            ".jpeg": image_loader,
            ".webp": image_loader,
        }
    )


def _create_loader(ingestion: Mapping[str, Any], image_root: str) -> BaseLoader:
    config = ingestion.get("loader", {})
    if not isinstance(config, Mapping):
        raise ValueError("Setting ingestion.loader must be a mapping")
    provider = config.get("provider", "markitdown")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("Setting ingestion.loader.provider must be non-empty")

    provider = provider.strip().lower()
    if provider == "markitdown":
        return PdfLoader(image_root=image_root)
    if provider == "mineru":
        token = os.environ.get("MINERU_API_TOKEN", "").strip()
        if not token:
            raise ValueError("MINERU_API_TOKEN is required for the MinerU loader")
        model = config.get("model_version", "vlm")
        if not isinstance(model, str):
            raise ValueError("Setting ingestion.loader.model_version must be text")
        return MinerUPdfLoader(
            token=token,
            image_root=image_root,
            model_version=model.strip(),
            poll_interval_seconds=_positive_float(
                config,
                "poll_interval_seconds",
                "ingestion.loader",
                default=3,
            ),
            poll_timeout_seconds=_positive_float(
                config,
                "poll_timeout_seconds",
                "ingestion.loader",
                default=900,
            ),
            request_timeout_seconds=_positive_float(
                config,
                "request_timeout_seconds",
                "ingestion.loader",
                default=120,
            ),
        )
    raise ValueError("Setting ingestion.loader.provider must be markitdown or mineru")


def _index_guard(settings: Settings, vector_store: Any) -> IndexFingerprintGuard | None:
    if str(settings.vector_store.get("backend", "")).strip().lower() != "chroma":
        return None
    stats = vector_store.get_collection_stats()
    guard = IndexFingerprintGuard(settings, existing_records=stats.chunk_count)
    guard.verify_config()
    return guard


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _positive_int(config: Mapping[str, Any], key: str, section: str) -> int:
    value = config.get(key)
    if value is None or isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {section}.{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    return parsed


def _optional_bool(
    config: Mapping[str, Any],
    key: str,
    section: str,
    *,
    default: bool,
) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be a boolean")
    return value


def _positive_float(
    config: Mapping[str, Any],
    key: str,
    section: str,
    *,
    default: float | None = None,
) -> float:
    value = config.get(key, default)
    if value is None or isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {section}.{key} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {section}.{key} must be positive")
    return parsed


__all__ = ["build_ingestion_pipeline"]
