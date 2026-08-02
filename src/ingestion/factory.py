"""Configuration-driven assembly for the local ingestion pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.settings import Settings
from src.ingestion.chunking import DocumentChunker
from src.ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from src.ingestion.pipeline import IngestionPipeline
from src.ingestion.storage import BM25Indexer, ImageStorage
from src.ingestion.transform import ChunkRefiner, ImageCaptioner, MetadataEnricher
from src.libs.loader import PdfLoader, SQLiteIntegrityStore
from src.libs.vector_store import create_vector_store


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
    vector_store = create_vector_store(settings)
    bm25_store = BM25Indexer(
        _required_text(storage, "bm25_path", "ingestion.storage"),
        generation_store=integrity,
    )
    image_store = ImageStorage(
        _required_text(storage, "image_db_path", "ingestion.storage"),
        image_root,
        generation_store=integrity,
    )

    return IngestionPipeline(
        integrity=integrity,
        loader=PdfLoader(image_root=image_root),
        chunker=DocumentChunker(settings),
        transforms=[
            ChunkRefiner(settings),
            MetadataEnricher(settings),
            ImageCaptioner(settings),
        ],
        batch_processor=BatchProcessor(
            DenseEncoder(settings),
            SparseEncoder(),
            batch_size=_positive_int(ingestion, "batch_size", "ingestion"),
        ),
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        claim_lease_seconds=_positive_float(
            ingestion,
            "claim_lease_seconds",
            "ingestion",
        ),
    )


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


def _positive_float(config: Mapping[str, Any], key: str, section: str) -> float:
    value = config.get(key)
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
