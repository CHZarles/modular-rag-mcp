"""将图片元数据解析为响应载荷。"""

from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

from src.core.types import ImagePayload, JsonDict, RetrievalCandidate


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
                image_id = _image_id(image)
                if image_id is None or image_id in seen:
                    continue
                payload = _payload_from_image(image, candidate.chunk_id)
                if payload is None:
                    continue
                seen.add(payload.image_id)
                payloads.append(payload)
        return payloads


def _payload_from_image(raw: object, chunk_id: str) -> ImagePayload | None:
    """容错解析单条图片元数据，非法记录直接忽略。"""
    if not isinstance(raw, dict):
        return None
    image_id = _image_id(raw)
    if image_id is None:
        return None
    path = raw.get("path")
    if not path:
        return None
    image_path = Path(str(path)).expanduser()
    try:
        image_data = image_path.read_bytes()
    except (OSError, ValueError):
        return None
    if not image_data:
        return None

    mime_type = _mime_type(raw.get("mime_type"), image_path)
    return ImagePayload(
        image_id=image_id,
        mime_type=mime_type,
        data_base64=base64.b64encode(image_data).decode("ascii"),
        uri=str(raw.get("uri") or image_path),
        source_ref=chunk_id,
        metadata={
            key: value
            for key, value in raw.items()
            if key not in {"image_id", "id", "mime_type", "uri", "path"}
        },
    )


def build_mcp_image_content(images: list[ImagePayload]) -> list[JsonDict]:
    """只用响应内嵌数据构造 MCP 图片块，不解析或读取 ``uri``。"""
    content: list[JsonDict] = []
    for image in images:
        encoded = _normalized_base64(image.data_base64)
        mime_type = image.mime_type.strip() if isinstance(image.mime_type, str) else ""
        if encoded is None or not mime_type.startswith("image/"):
            continue
        content.append(
            {
                "type": "image",
                "data": encoded,
                "mimeType": mime_type,
            }
        )
    return content


def _image_id(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    image_id = raw.get("image_id", raw.get("id"))
    return str(image_id) if image_id else None


def _mime_type(raw_mime_type: object, path: Path) -> str:
    if isinstance(raw_mime_type, str) and raw_mime_type.strip().startswith("image/"):
        return raw_mime_type.strip()
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is not None and guessed.startswith("image/"):
        return guessed
    return "image/png"


def _normalized_base64(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    encoded = raw.strip()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or not header.lower().endswith(";base64"):
            return None
        encoded = encoded.strip()
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not decoded:
        return None
    return base64.b64encode(decoded).decode("ascii")
