"""Azure OpenAI Vision 客户端及图片尺寸预处理。"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from src.libs.llm.azure_llm import AzureOpenAILLM
from src.libs.llm.openai_llm import (
    JsonObject,
    _image_data_url,
    _read_content,
    _serialize_messages,
)
from src.ports.llm import (
    ChatResponse,
    ImageInput,
    ImagePreprocessor,
    Message,
    preprocess_image,
)

_DEFAULT_MAX_IMAGE_SIZE = 2048
_OUTPUT_FORMATS = {
    "JPEG": ("JPEG", "image/jpeg"),
    "PNG": ("PNG", "image/png"),
    "WEBP": ("WEBP", "image/webp"),
}


class AzureVisionLLM(AzureOpenAILLM):
    """通过 Azure OpenAI deployment 调用视觉模型。"""

    def __init__(self, config: Mapping[str, Any]) -> None:
        endpoint = config.get("azure_endpoint") or config.get("endpoint") or config.get(
            "base_url"
        )
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("azure vision configuration error: missing azure_endpoint")

        normalized = dict(config)
        normalized["endpoint"] = endpoint
        self.max_image_size = _positive_image_size(config)
        super().__init__(normalized)

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        preprocessor: ImagePreprocessor | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """压缩超大图片后发送 Azure Chat Completions 多模态请求。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("azure input error: text must not be empty")

        image = preprocess_image(image, preprocessor)
        image = _resize_image(image, self.max_image_size)
        serialized: list[JsonObject] = (
            list(_serialize_messages(messages, self.provider)) if messages else []
        )
        serialized.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image)},
                    },
                ],
            }
        )
        payload: JsonObject = {"messages": serialized, **dict(kwargs)}
        raw = self._post_json(self._chat_url(), payload)
        content = _read_content(raw, self.provider)
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        return ChatResponse(
            content=content,
            model=str(raw.get("model") or self.model),
            usage=usage,
            raw_response=raw,
        )


def _positive_image_size(config: Mapping[str, Any]) -> int:
    value = config.get("max_image_size", _DEFAULT_MAX_IMAGE_SIZE)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("azure vision max_image_size must be a positive integer")
    return value


def _resize_image(image: ImageInput, max_image_size: int) -> ImageInput:
    normalized, data = _normalized_image(image)
    try:
        with Image.open(BytesIO(data)) as source:
            output = ImageOps.exif_transpose(source)
            if max(output.size) <= max_image_size:
                return normalized

            output.thumbnail(
                (max_image_size, max_image_size),
                Image.Resampling.LANCZOS,
            )
            image_format, mime_type = _OUTPUT_FORMATS.get(
                str(source.format).upper(),
                ("PNG", "image/png"),
            )
            if image_format == "JPEG" and output.mode not in ("RGB", "L"):
                output = output.convert("RGB")
            buffer = BytesIO()
            output.save(buffer, format=image_format, optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"azure vision image error: unsupported image data: {exc}") from exc
    return ImageInput(data=buffer.getvalue(), mime_type=mime_type)


def _normalized_image(image: ImageInput) -> tuple[ImageInput, bytes]:
    if image.path is not None:
        try:
            return image, Path(image.path).read_bytes()
        except OSError as exc:
            raise ValueError(f"azure vision image error: cannot read {image.path}: {exc}") from exc
    if image.data is not None:
        return image, image.data

    encoded = str(image.base64).strip()
    mime_type = image.mime_type
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("azure vision image error: invalid Base64 data URL")
        mime_type = header[5:].split(";", 1)[0] or mime_type
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("azure vision image error: invalid Base64 data") from exc
    return ImageInput(base64=encoded, mime_type=mime_type), data


__all__ = ["AzureVisionLLM"]
