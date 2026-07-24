"""Image Captioner — uses Vision LLM to add captions to chunks.

When no vision LLM is configured (or the LLM raises), the transform is a
no-op pass-through. This honours the spec's "禁用/失败降级" requirement.
"""

from __future__ import annotations

from typing import Any

from src.core.types import Chunk
from src.ports.ingestion import BaseTransform
from src.ports.llm import BaseVisionLLM, ImageInput


class ImageCaptioner(BaseTransform):
    """Calls a Vision LLM per image; failures fall back gracefully."""

    name = "image_captioner"

    def __init__(self, vision_llm: BaseVisionLLM | None = None) -> None:
        self.vision_llm = vision_llm

    def transform(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[Chunk]:
        if self.vision_llm is None:
            return chunks
        out: list[Chunk] = []
        for chunk in chunks:
            images = chunk.metadata.get("images", [])
            if not isinstance(images, list) or not images:
                out.append(chunk)
                continue
            new_text, captions = self._caption_images(chunk.text, images)
            metadata = dict(chunk.metadata)
            metadata["image_captions"] = captions
            out.append(
                Chunk(
                    id=chunk.id,
                    text=new_text,
                    metadata=metadata,
                    source_ref=chunk.source_ref,
                    chunk_index=chunk.chunk_index,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                )
            )
        return out

    def _caption_images(
        self, text: str, images: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, str]]]:
        captions: list[dict[str, str]] = []
        appended: list[str] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            image_id = str(image.get("id", ""))
            image_path = image.get("path")
            try:
                response = self.vision_llm.chat_with_image(
                    text=f"Describe this image in 1-2 sentences for retrieval.",
                    image=ImageInput(path=str(image_path)) if image_path else ImageInput(base64=""),
                    trace=None,
                )
                caption = response.content.strip()
            except Exception:  # noqa: BLE001 — graceful degradation per spec
                caption = ""
            captions.append({"id": image_id, "caption": caption})
            if caption:
                appended.append(f"[{image_id}] {caption}")
        if appended:
            return text.rstrip() + "\n\n" + "\n".join(appended), captions
        return text, captions