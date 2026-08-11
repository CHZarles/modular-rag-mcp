from __future__ import annotations

from core.settings import Settings
from observability.dashboard._ingestion_helpers import (
    collection_options,
)
from src.application.upload_ingestion import settings_for_ingestion_profile


def test_fast_profile_keeps_document_image_captioning_without_mutating_settings() -> None:
    settings = _settings()

    fast = settings_for_ingestion_profile(settings, ai_enrichment=False)

    assert fast.ingestion["chunk_refiner"]["use_llm"] is False
    assert fast.ingestion["metadata_enricher"]["use_llm"] is False
    assert fast.ingestion["image_captioner"]["enabled"] is True
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
