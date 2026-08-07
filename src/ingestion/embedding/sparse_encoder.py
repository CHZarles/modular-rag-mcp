"""为 BM25 索引生成确定性的词频统计。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from src.core.index_fingerprint import TOKENIZER_VERSION
from src.core.types import Chunk, JsonDict

_TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+(?:[_+#.-][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.IGNORECASE,
)
_CJK_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


class SparseEncoder:
    """把每个 Chunk 编码为 C11 BM25Indexer 可直接消费的局部统计。"""

    def encode(
        self,
        chunks: list[Chunk],
        trace: Any | None = None,
    ) -> list[JsonDict]:
        """按 Chunk 顺序返回词频映射和分词后的文档长度。"""
        statistics: list[JsonDict] = []
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            terms = dict(Counter(tokens))
            statistics.append({"terms": terms, "doc_length": len(tokens)})
        return statistics


def tokenize(text: str) -> list[str]:
    """规范化中英文文本；中文连续文本使用重叠二元词组。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(normalized):
        token = match.group(0)
        if not _CJK_PATTERN.fullmatch(token) or len(token) == 1:
            tokens.append(token)
            continue

        # 无额外分词依赖时，二元词组比逐字或整句更适合中文局部关键词召回。
        tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


__all__ = ["SparseEncoder", "TOKENIZER_VERSION", "tokenize"]
