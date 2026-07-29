"""把纯文本 Splitter 的结果转换为摄取链路使用的 Chunk。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.core.types import Chunk, Document, JsonDict
from src.libs.splitter import SplitterFactory
from src.ports.ingestion import BaseSplitter

_IMAGE_PLACEHOLDER = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")


class DocumentChunker:
    """为切分结果补充稳定 ID、来源、偏移量和按块分发的图片引用。"""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        splitter: BaseSplitter | None = None,
    ) -> None:
        """从配置创建 Splitter；测试和上层装配也可直接注入实现。

        ``DocumentChunker(FakeSplitter())`` 是早期装配代码使用的形式，因此仍按
        BaseSplitter Protocol 识别该位置参数，避免破坏现有调用方。
        """
        if splitter is None and isinstance(settings, BaseSplitter):
            splitter = settings
        self.splitter = splitter if splitter is not None else SplitterFactory.create(settings)

    def split_document(self, document: Document, trace: Any | None = None) -> list[Chunk]:
        """切分文档，并把纯字符串结果转换成可追溯的 Chunk。"""
        texts = self.splitter.split_text(document.text, trace=trace)
        chunks: list[Chunk] = []
        search_from = 0

        for index, text in enumerate(texts):
            # 从上一块起点之后继续查找，兼容 Splitter 产生的重叠文本块。
            found_start = document.text.find(text, search_from)
            if found_start < 0:
                start_offset = None
                end_offset = None
            else:
                start_offset = found_start
                end_offset = found_start + len(text)
                search_from = found_start + 1

            metadata = self._inherit_metadata(document, index, text)
            chunk = Chunk(
                id=self._generate_chunk_id(document.id, index, text),
                text=text,
                metadata=metadata,
                source_ref=document.id,
                chunk_index=index,
                start_offset=start_offset,
                end_offset=end_offset,
            )
            chunks.append(chunk)

        return chunks

    @staticmethod
    def _inherit_metadata(
        document: Document,
        chunk_index: int,
        chunk_text: str,
    ) -> JsonDict:
        """复制文档元数据，并只保留当前文本实际引用的图片。"""
        metadata = dict(document.metadata)
        metadata["chunk_index"] = chunk_index

        # 文档级 images 不能整体继承，否则下游会为每个 Chunk 重复处理全部图片。
        document_images = metadata.pop("images", [])
        metadata.pop("image_refs", None)
        image_ids = [match.group(1).strip() for match in _IMAGE_PLACEHOLDER.finditer(chunk_text)]
        if not image_ids:
            return metadata

        metadata["image_refs"] = image_ids
        images_by_id: JsonDict = {}
        if isinstance(document_images, list):
            for image in document_images:
                if not isinstance(image, dict):
                    continue
                image_id = image.get("image_id", image.get("id"))
                if image_id:
                    images_by_id[str(image_id)] = image

        matched_images = [images_by_id[image_id] for image_id in image_ids if image_id in images_by_id]
        if matched_images:
            metadata["images"] = matched_images

        return metadata

    @staticmethod
    def _generate_chunk_id(document_id: str, index: int, text: str) -> str:
        """使用文档、顺序和内容生成可复现的 Chunk ID。"""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{document_id}_{index:04d}_{digest[:8]}"
