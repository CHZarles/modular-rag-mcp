"""Metadata filtering helpers."""

from __future__ import annotations

from src.core.types import JsonDict, RetrievalCandidate


class ExactMetadataFilter:
    """Small default filter using exact metadata equality."""

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
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage(
                "metadata_filter",
                {"input_count": len(candidates), "output_count": len(filtered), "filters": filters},
            )
        return filtered
