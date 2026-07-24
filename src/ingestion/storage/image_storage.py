"""Image storage — minimal in-memory index over image references."""

from __future__ import annotations

from typing import Any

from src.core.types import ImageRef


class ImageStorage:
    """Holds ImageRef records; no actual blob storage in the clean-start skeleton."""

    def __init__(self) -> None:
        self._images: dict[str, ImageRef] = {}

    def save_refs(
        self,
        images: list[ImageRef],
        trace: Any | None = None,
    ) -> None:
        for img in images:
            self._images[img.image_id] = img

    def get(self, image_id: str) -> ImageRef | None:
        return self._images.get(image_id)

    def list_by_document(
        self,
        source_path: str,
        collection: str,
    ) -> list[ImageRef]:
        return [
            img
            for img in self._images.values()
            if img.source_path == source_path and img.collection == collection
        ]

    def delete_by_document(
        self,
        source_path: str,
        collection: str,
    ) -> int:
        before = len(self._images)
        self._images = {
            k: v
            for k, v in self._images.items()
            if not (v.source_path == source_path and v.collection == collection)
        }
        return before - len(self._images)