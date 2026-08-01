"""元数据过滤辅助组件。"""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate


class ExactMetadataFilter:
    """按元数据字段精确匹配的默认过滤器。"""

    def apply(
        self,
        candidates: list[RetrievalCandidate],
        filters: JsonDict,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        if not filters:
            return list(candidates)
        filtered = [
            candidate
            for candidate in candidates
            if all(candidate.metadata.get(key) == value for key, value in filters.items())
        ]
        return filtered
