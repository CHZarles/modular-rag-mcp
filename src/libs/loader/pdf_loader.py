"""使用 MarkItDown 解析正文、PyMuPDF 提取图片的 PDF Loader。

一次加载分为两条相互独立的路径：

1. MarkItDown 把 PDF 正文转换为适合后续 Markdown Splitter 的文本；正文失败时整个
   加载失败，因为没有可检索内容。
2. PyMuPDF 提取嵌入图片、页码和 PDF 坐标；图片失败时记录警告并降级为空列表，不
   阻塞正文摄取。

最终返回的 ``Document.id`` 是文件 SHA256，metadata 至少包含 ``source_path``、
``collection``、``doc_type``、``title`` 和 ``images``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
from markitdown import MarkItDown

from src.core.types import Document, JsonDict
from src.libs.loader.file_integrity import compute_sha256
from src.observability.logger import get_logger

logger = get_logger(__name__)


class PdfLoader:
    """把单个 PDF 转换成 Markdown 文本和统一图片引用。

    ``converter`` 参数用于测试或替换 MarkItDown 实例；正常运行无需传入。``trace``
    来自统一 Loader Protocol，当前阶段尚未在此处记录可观测事件。
    """

    supported_extensions = (".pdf",)

    def __init__(
        self,
        image_root: str | Path = "data/images",
        converter: Any | None = None,
    ) -> None:
        # image_root 只负责图片根目录，collection 会在加载时作为下一层目录加入。
        self.image_root = Path(image_root).expanduser()
        self._converter = converter or MarkItDown()

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        """解析 PDF；图片提取失败时保留正文并降级返回。"""
        path = Path(source_path).expanduser()

        # 在调用第三方解析器前完成输入校验，使错误信息保持稳定且容易定位。
        if path.suffix.lower() not in self.supported_extensions:
            raise ValueError(f"pdf loader input error: unsupported file type {path.suffix!r}")
        if not path.is_file():
            raise FileNotFoundError(f"pdf loader input error: file not found: {path}")

        # collection 会参与磁盘路径拼接，只接受单层名称以阻止 ../ 等路径穿越。
        if (
            not collection.strip()
            or Path(collection).name != collection
            or collection in {".", ".."}
        ):
            raise ValueError("pdf loader input error: collection must be a simple name")

        # 与 FileIntegrity 使用同一算法，确保 Document ID、增量检查和图片 ID 可对齐。
        file_hash = compute_sha256(path)
        try:
            # MarkItDown 的 text_content 是本 Loader 的规范正文输出。
            text = str(self._converter.convert(str(path)).text_content).strip()
        except Exception as exc:
            # 隔离第三方异常类型，上层只依赖稳定的 Loader 错误语义。
            raise RuntimeError(f"pdf loader conversion failed: {path}") from exc

        try:
            images = self._extract_images(path, collection, file_hash)
        except Exception as exc:
            # 图片是增强信息，失败不应阻断可检索正文的摄取。
            logger.warning("PDF image extraction failed for %s: %s", path, exc)
            images = []

        # 占位符和 text_offset 必须基于最终文本计算，不能使用 PDF 字节位置。
        text = _insert_image_placeholders(text, images)

        # Loader 只负责标准化，不在这里进行切分、Embedding 或存储。
        return Document(
            id=file_hash,
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "pdf",
                "title": path.stem,
                "images": images,
            },
        )

    def _extract_images(
        self,
        path: Path,
        collection: str,
        file_hash: str,
    ) -> list[JsonDict]:
        """按页提取嵌入图片，统一转成 PNG 并返回位置元数据。

        图片 ID 由文件哈希、从 1 开始的页码和页内出现序号组成；同一 PDF 重复加载
        会写到同一路径，实现自然幂等。PyMuPDF 的版面块会为每次图片绘制分别返回
        一条记录，因此同一个底层图片资源出现多次时不会丢失位置。
        """
        output_dir = self.image_root / collection
        images: list[JsonDict] = []
        with pymupdf.open(path) as pdf:
            for page_index, page in enumerate(pdf):
                # dict 版面块按阅读顺序同时给出文本块和每次图片出现的位置。
                blocks = page.get_text("dict", sort=True).get("blocks", [])
                image_blocks = (
                    (block_index, block)
                    for block_index, block in enumerate(blocks)
                    if block.get("type") == 1
                )
                for sequence, (block_index, block) in enumerate(image_blocks, start=1):
                    image_id = f"{file_hash}_{page_index + 1}_{sequence}"
                    image_path = output_dir / f"{image_id}.png"
                    output_dir.mkdir(parents=True, exist_ok=True)

                    # 每个图片块直接携带当前出现对应的图像数据，可避免复用 xref 时错位。
                    pixmap = pymupdf.Pixmap(block["image"])
                    if pixmap.colorspace is not None and pixmap.colorspace.n > 3:
                        # PNG 常用 RGB；多于三个颜色通道时先转换，避免编码器拒绝。
                        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
                    image_path.write_bytes(pixmap.tobytes("png"))

                    images.append(
                        {
                            "id": image_id,
                            "path": str(image_path),
                            "page": page_index + 1,
                            "text_offset": 0,
                            "text_length": 0,
                            "position": _rect_to_dict(pymupdf.Rect(block["bbox"])),
                            # 两个临时锚点用于映射 MarkItDown 文本，插入后会从元数据移除。
                            "_before_text": _neighbor_text(blocks, block_index, -1),
                            "_after_text": _neighbor_text(blocks, block_index, 1),
                        }
                    )
        return images


def _insert_image_placeholders(text: str, images: list[JsonDict]) -> str:
    """依据相邻文本锚点插入图片占位符，并写回最终 Markdown 偏移量。

    MarkItDown 不提供 PDF 坐标到 Markdown 字符位置的映射，所以这里使用 PyMuPDF
    版面顺序中的前后文本作为锚点。锚点无法在转换结果中匹配时才降级追加到末尾，
    保证图片引用不会丢失。
    """
    result = text
    cursor = 0
    for image in images:
        before_text = str(image.pop("_before_text", ""))
        after_text = str(image.pop("_after_text", ""))
        placeholder = f"[IMAGE: {image['id']}]"
        insertion_offset = _find_insertion_offset(result, before_text, after_text, cursor)
        prefix = _missing_newlines_before(result, insertion_offset)
        suffix = _missing_newlines_after(result, insertion_offset)

        # offset 指向最终 Document.text，而不是插入前的临时字符串。
        image["text_offset"] = insertion_offset + len(prefix)
        image["text_length"] = len(placeholder)
        result = (
            result[:insertion_offset]
            + prefix
            + placeholder
            + suffix
            + result[insertion_offset:]
        )
        cursor = int(image["text_offset"]) + len(placeholder)
    return result


def _neighbor_text(blocks: list[JsonDict], image_index: int, direction: int) -> str:
    """返回图片前一块的末行或后一块的首行文本，跳过相邻图片块。"""
    index = image_index + direction
    while 0 <= index < len(blocks):
        block = blocks[index]
        if block.get("type") == 0:
            lines = [
                "".join(str(span.get("text", "")) for span in line.get("spans", [])).strip()
                for line in block.get("lines", [])
            ]
            nonempty_lines = [line for line in lines if line]
            if nonempty_lines:
                return nonempty_lines[-1] if direction < 0 else nonempty_lines[0]
        index += direction
    return ""


def _find_insertion_offset(text: str, before: str, after: str, cursor: int) -> int:
    """在已处理位置之后查找锚点；找不到时返回文末作为降级位置。"""
    before_offset = text.find(before, cursor) if before else -1
    search_from = before_offset + len(before) if before_offset >= 0 else cursor
    after_offset = text.find(after, search_from) if after else -1
    if after_offset >= 0:
        return after_offset
    if before_offset >= 0:
        return before_offset + len(before)
    return len(text)


def _missing_newlines_before(text: str, offset: int) -> str:
    """补足占位符前的 Markdown 段落分隔符。"""
    if offset == 0:
        return ""
    trailing = len(text[:offset]) - len(text[:offset].rstrip("\n"))
    return "\n" * max(0, 2 - trailing)


def _missing_newlines_after(text: str, offset: int) -> str:
    """补足占位符后的 Markdown 段落分隔符。"""
    if offset == len(text):
        return ""
    leading = len(text[offset:]) - len(text[offset:].lstrip("\n"))
    return "\n" * max(0, 2 - leading)


def _rect_to_dict(rect: Any) -> JsonDict:
    """把 PyMuPDF Rect 转为可 JSON 序列化、与供应商无关的坐标字典。"""
    return {
        "x0": float(rect.x0),
        "y0": float(rect.y0),
        "x1": float(rect.x1),
        "y1": float(rect.y1),
    }


__all__ = ["PdfLoader"]
