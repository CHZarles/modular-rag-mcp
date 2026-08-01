"""最小查询预处理器。

这里只保留稳定的分词与过滤参数合并逻辑，避免掺入过多文本清洗策略，确保默认行为
简单且可预测。
"""

from __future__ import annotations

import re
import unicodedata

from src.core.types import ProcessedQuery, QueryRequest

_TOKEN_RE = re.compile(
    r"[a-z0-9]+(?:[_+#.-][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "的",
    "了",
    "和",
    "与",
    "是",
    "在",
}


class QueryProcessor:
    """规范化独立查询，并为 Dense 与 Sparse 两路准备各自输入。"""

    def process(self, request: QueryRequest, trace: object | None = None) -> ProcessedQuery:
        """保留完整语义查询，同时生成关键词和独立的过滤条件副本。"""
        standalone_query = " ".join(request.query.strip().split())
        keywords = _extract_keywords(standalone_query)
        processed = ProcessedQuery(
            original_query=request.query,
            standalone_query=standalone_query,
            keywords=keywords,
            filters=dict(request.filters),
        )
        return processed


def _extract_keywords(text: str) -> list[str]:
    """提取中英文及技术词元，去除停用词并按首次出现顺序去重。

    中文连续文本暂时保留为词元，交给 BM25 与写入阶段共用的分词规则继续拆分；这里
    不再实现第二套中文切词算法。若查询全部由停用词组成，则退回原始词元，避免一个
    原本有内容的查询因为规则过强而失去 Sparse 召回机会。
    """
    normalized_text = unicodedata.normalize("NFKC", text)
    tokens = _TOKEN_RE.findall(normalized_text)
    informative_tokens = [
        token for token in tokens if token.casefold() not in _STOP_WORDS
    ]
    selected_tokens = informative_tokens or tokens

    seen: set[str] = set()
    keywords: list[str] = []
    for token in selected_tokens:
        normalized = token.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(token)
    return keywords
