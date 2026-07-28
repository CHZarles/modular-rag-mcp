"""将图片元数据解析为响应载荷。"""

from __future__ import annotations

from src.core.types import ImagePayload, RetrievalCandidate


class MultimodalAssembler:
    """从候选 Chunk 中提取并去重图片引用。"""

    def resolve_images(
        self,
        candidates: list[RetrievalCandidate],
        include_images: bool,
    ) -> list[ImagePayload]:
        if not include_images:
            return []

        payloads: list[ImagePayload] = []
        # 同一图片可能被相邻 Chunk 重复引用，只向客户端返回一次。
        seen: set[str] = set()
        for candidate in candidates:
            images = candidate.metadata.get("images", [])
            if not isinstance(images, list):
                continue
            for image in images:
                payload = _payload_from_image(image, candidate.chunk_id)
                if payload is None or payload.image_id in seen:
                    continue
                seen.add(payload.image_id)
                payloads.append(payload)
        return payloads


def _payload_from_image(raw: object, chunk_id: str) -> ImagePayload | None:
    """容错解析单条图片元数据，非法记录直接忽略。"""
    if not isinstance(raw, dict):
        return None
    image_id = raw.get("image_id", raw.get("id"))
    if not image_id:
        return None
    path = raw.get("path")
    uri = raw.get("uri") or (str(path) if path else None)
    return ImagePayload(
        image_id=str(image_id),
        mime_type=str(raw.get("mime_type", "image/png")),
        uri=uri,
        source_ref=chunk_id,
        metadata={
            key: value
            for key, value in raw.items()
            if key not in {"image_id", "id", "mime_type", "uri", "path"}
        },
    )
