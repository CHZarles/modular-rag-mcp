"""Shared ingestion helpers used by the Dashboard API and other internal callers."""

from __future__ import annotations

from src.core.settings import Settings


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


__all__ = [
    "collection_options",
    "dashboard_ai_enrichment_default",
]
