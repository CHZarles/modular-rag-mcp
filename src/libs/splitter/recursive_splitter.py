"""面向 Markdown 的递归文本切分器。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter


class RecursiveSplitter:
    """按 Markdown 结构优先级切分文本，并保留相邻片段重叠。"""

    def __init__(self, config: Mapping[str, Any]) -> None:
        chunk_size = int(config.get("chunk_size", 1000))
        chunk_overlap = int(config.get("chunk_overlap", 200))
        if chunk_size <= 0:
            raise ValueError("recursive splitter configuration error: chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError(
                "recursive splitter configuration error: chunk_overlap must be non-negative "
                "and smaller than chunk_size"
            )

        self._splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.MARKDOWN,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            keep_separator=True,
            strip_whitespace=True,
        )

    def split_text(self, text: str, trace: Any | None = None) -> list[str]:
        """切分非空 Markdown；空白输入不产生 chunk。"""
        if not isinstance(text, str):
            raise TypeError("recursive splitter input error: text must be str")
        if not text.strip():
            return []
        return self._splitter.split_text(text)


__all__ = ["RecursiveSplitter"]
