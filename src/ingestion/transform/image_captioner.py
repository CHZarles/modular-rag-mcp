"""通过可注入的视觉模型为 Chunk 中的图片生成可检索描述。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.types import Chunk, ImageRef, JsonDict
from src.libs.llm import BaseVisionLLM, ImageInput, create_llm

_DEFAULT_PROMPT = """请描述图片中与知识检索有关的信息。
重点说明可见文字、结构、流程、数据关系和结论，不添加图片中不存在的内容。
只返回图片描述，不输出思考过程或说明。

图片编号：{image_id}
图片所在文档块：
{context}
"""
_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config/prompts/image_captioning.txt"
_THINK_BLOCK_PATTERN = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)


class ImageCaptioner:
    """生成图片描述；禁用、缺少客户端或单图失败时保留原图片引用。"""

    name = "image_captioner"

    def __init__(
        self,
        settings: Any,
        vision_llm: BaseVisionLLM | None = None,
        prompt_path: str | Path | None = None,
    ) -> None:
        self._enabled = _read_enabled(settings)
        self._vision_llm = vision_llm
        self._prompt = self._load_prompt(prompt_path)

        # MiniMax M3 复用现有 OpenAI-compatible 客户端，无需单独配置 Vision Provider。
        if self._enabled and self._vision_llm is None:
            try:
                candidate = create_llm(settings)
            except Exception:
                candidate = None
            if isinstance(candidate, BaseVisionLLM):
                self._vision_llm = candidate

    def transform(self, chunks: list[Chunk], trace: Any | None = None) -> list[Chunk]:
        """逐块处理图片，单张图片失败不会影响同块其他图片或后续 Chunk。"""
        results: list[Chunk] = []
        for chunk in chunks:
            refs = _image_refs(chunk.metadata.get("image_refs"))
            if not refs:
                results.append(chunk)
                continue
            if (
                chunk.metadata.get("image_captioned_by") == "vision_llm"
                and chunk.metadata.get("has_unprocessed_images") is False
            ):
                results.append(chunk)
                continue

            try:
                results.append(self._caption_chunk(chunk, refs, trace))
            except Exception as exc:
                # 异常隔离在 Chunk 边界；失败信息只进入 metadata，不中止摄取。
                metadata = {
                    **chunk.metadata,
                    "has_unprocessed_images": True,
                    "unprocessed_image_refs": refs,
                    "image_caption_error": str(exc),
                }
                results.append(replace(chunk, metadata=metadata))
        return results

    def _caption_chunk(
        self,
        chunk: Chunk,
        refs: list[str],
        trace: Any | None,
    ) -> Chunk:
        if not self._enabled or self._vision_llm is None:
            return _with_unprocessed_images(chunk, refs)

        images = _images_by_id(chunk.metadata.get("images"))
        # 部分失败后的重试复用已成功描述，只再次请求尚未处理的图片。
        captions = _existing_captions(chunk.metadata.get("image_captions"))
        unprocessed: list[str] = []
        text = chunk.text

        for image_id in refs:
            if image_id in captions:
                continue
            image = images.get(image_id)
            if image is None or not image.path.is_file():
                unprocessed.append(image_id)
                continue

            caption = self._caption_image(image_id, image, chunk.text, trace)
            if caption is None:
                unprocessed.append(image_id)
                continue
            captions[image_id] = caption
            text = _inject_caption(text, image_id, caption)

        metadata = dict(chunk.metadata)
        metadata.pop("image_caption_error", None)
        if captions:
            metadata["image_captions"] = captions
            metadata["image_captioned_by"] = "vision_llm"
        else:
            metadata.pop("image_captions", None)
            metadata.pop("image_captioned_by", None)

        metadata["has_unprocessed_images"] = bool(unprocessed)
        if unprocessed:
            metadata["unprocessed_image_refs"] = unprocessed
        else:
            metadata.pop("unprocessed_image_refs", None)
        return replace(chunk, text=text, metadata=metadata)

    def _caption_image(
        self,
        image_id: str,
        image: _ImageSource,
        context: str,
        trace: Any | None,
    ) -> str | None:
        """调用视觉模型；接口异常和空描述统一视为当前图片处理失败。"""
        if self._vision_llm is None:
            return None
        prompt = self._prompt.format(image_id=image_id, context=context)
        try:
            response = self._vision_llm.chat_with_image(
                prompt,
                ImageInput(path=image.path, mime_type=image.mime_type),
                trace=trace,
            )
        except Exception:
            return None
        caption = _THINK_BLOCK_PATTERN.sub("", response.content).strip()
        return caption or None

    def _load_prompt(self, prompt_path: str | Path | None = None) -> str:
        """读取 Prompt；缺失、为空或缺少上下文占位符时使用内置模板。"""
        path = Path(prompt_path) if prompt_path is not None else _PROMPT_PATH
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except OSError:
            return _DEFAULT_PROMPT
        required = ("{image_id}", "{context}")
        return prompt if prompt and all(marker in prompt for marker in required) else _DEFAULT_PROMPT


class _ImageSource:
    """ImageCaptioner 内部使用的最小本地图片输入。"""

    def __init__(self, path: Path, mime_type: str) -> None:
        self.path = path
        self.mime_type = mime_type


def _images_by_id(value: Any) -> dict[str, _ImageSource]:
    if not isinstance(value, list):
        return {}
    images: dict[str, _ImageSource] = {}
    for item in value:
        if isinstance(item, ImageRef):
            images[item.image_id] = _ImageSource(Path(item.path), item.mime_type)
            continue
        if not isinstance(item, Mapping):
            continue
        image_id = item.get("image_id", item.get("id"))
        path = item.get("path")
        if image_id and isinstance(path, str) and path.strip():
            images[str(image_id)] = _ImageSource(
                Path(path),
                str(item.get("mime_type") or "image/png"),
            )
    return images


def _image_refs(value: Any) -> list[str]:
    """规范化图片 ID，并在保持原顺序的同时去重。"""
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        image_id = str(item).strip() if isinstance(item, str | int) else ""
        if image_id and image_id not in refs:
            refs.append(image_id)
    return refs


def _existing_captions(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(image_id): caption.strip()
        for image_id, caption in value.items()
        if isinstance(caption, str) and caption.strip()
    }


def _inject_caption(text: str, image_id: str, caption: str) -> str:
    """把图片占位符替换成描述，使后续文本 Embedding 能检索图片语义。"""
    placeholder = re.compile(rf"\[IMAGE:\s*{re.escape(image_id)}\s*\]")
    replacement = f"[图片描述: {caption}]"
    enriched, count = placeholder.subn(lambda _: replacement, text)
    if count:
        return enriched
    separator = "\n\n" if enriched else ""
    return f"{enriched}{separator}{replacement}"


def _with_unprocessed_images(chunk: Chunk, refs: list[str]) -> Chunk:
    captions = _existing_captions(chunk.metadata.get("image_captions"))
    pending = [image_id for image_id in refs if image_id not in captions]
    metadata: JsonDict = {
        **chunk.metadata,
        "has_unprocessed_images": bool(pending),
    }
    if pending:
        metadata["unprocessed_image_refs"] = pending
    else:
        metadata.pop("unprocessed_image_refs", None)
    return replace(chunk, metadata=metadata)


def _read_enabled(settings: Any) -> bool:
    ingestion = settings.get("ingestion", {}) if isinstance(settings, Mapping) else getattr(
        settings, "ingestion", {}
    )
    if not isinstance(ingestion, Mapping):
        return False
    config = ingestion.get("image_captioner", {})
    if not isinstance(config, Mapping):
        return False
    value = config.get("enabled", config.get("use_llm", False))
    return value if isinstance(value, bool) else str(value).strip().lower() == "true"


__all__ = ["ImageCaptioner"]
