"""最小查询预处理器。

这里只保留稳定的分词与过滤参数合并逻辑，避免掺入过多文本清洗策略，确保默认行为
简单且可预测。
"""

from __future__ import annotations

import re

from src.core.types import ProcessedQuery, QueryRequest

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class QueryProcessor:
    """规范化查询文本并提取去重后的关键词。"""

    def process(self, request: QueryRequest, trace: object | None = None) -> ProcessedQuery:
        standalone_query = " ".join(request.query.strip().split())
        keywords = _extract_keywords(standalone_query)
        processed = ProcessedQuery(
            original_query=request.query,
            standalone_query=standalone_query,
            keywords=keywords,
            filters=dict(request.filters),
        )
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("query_process", processed.to_dict())
        return processed


def _extract_keywords(text: str) -> list[str]:
    """按首次出现顺序提取中英文词元，并忽略大小写重复项。"""
    seen: set[str] = set()
    keywords: list[str] = []
    for token in _TOKEN_RE.findall(text):
        normalized = token.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(token)
    return keywords
