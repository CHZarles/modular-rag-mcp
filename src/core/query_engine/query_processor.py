"""Minimal query processor.

The old implementation mixed useful token extraction with too much text
cleanup policy. This version keeps the interface stable and the default
behavior predictable.
"""

from __future__ import annotations

import re

from src.core.types import ProcessedQuery, QueryRequest

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class QueryProcessor:
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
    seen: set[str] = set()
    keywords: list[str] = []
    for token in _TOKEN_RE.findall(text):
        normalized = token.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(token)
    return keywords
