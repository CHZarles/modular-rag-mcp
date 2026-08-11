"""Route supported file extensions to concrete document loaders."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.core.types import Document
from src.ports.ingestion import BaseLoader

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".csv", ".png", ".jpg", ".jpeg", ".webp")
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class FormatRouter:
    """Select one loader by suffix while preserving the existing loader interface."""

    def __init__(self, loaders_by_extension: Mapping[str, BaseLoader]) -> None:
        self._loaders = {
            _normalize_extension(extension): loader
            for extension, loader in loaders_by_extension.items()
        }
        if not self._loaders:
            raise ValueError("format router configuration error: at least one loader is required")
        self.supported_extensions = tuple(sorted(self._loaders))
        revisions = [
            f"{extension}={getattr(loader, 'revision', type(loader).__name__)}"
            for extension, loader in sorted(self._loaders.items())
        ]
        self.revision = "format-router-v1|" + "|".join(revisions)

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        extension = Path(source_path).suffix.lower()
        loader = self._loaders.get(extension)
        if loader is None:
            raise ValueError(
                f"format router input error: unsupported file type {extension!r}"
            )
        return loader.load(source_path, collection, trace=trace)


def _normalize_extension(extension: str) -> str:
    normalized = extension.strip().lower()
    if not normalized.startswith(".") or normalized == ".":
        raise ValueError(f"format router configuration error: invalid extension {extension!r}")
    return normalized


__all__ = ["FormatRouter", "IMAGE_EXTENSIONS", "SUPPORTED_EXTENSIONS"]
