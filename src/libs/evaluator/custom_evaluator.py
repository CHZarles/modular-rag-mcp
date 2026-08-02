"""无需外部服务的检索质量指标。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.types import EvaluationCase, QueryResponse


class CustomEvaluator:
    """根据黄金 chunk ID 计算 Hit Rate 和 MRR。"""

    name = "custom"

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        metrics = _source_metrics(case, response)
        golden_ids = set(case.expected_chunk_ids)
        if not golden_ids:
            return {"hit_rate": 0.0, "mrr": 0.0, **metrics}

        first_relevant_rank = next(
            (
                rank
                for rank, item in enumerate(response.items, start=1)
                if item.chunk_id in golden_ids
            ),
            None,
        )
        if first_relevant_rank is None:
            return {"hit_rate": 0.0, "mrr": 0.0, **metrics}
        return {"hit_rate": 1.0, "mrr": 1.0 / first_relevant_rank, **metrics}


def _source_metrics(case: EvaluationCase, response: QueryResponse) -> dict[str, float]:
    raw_sources = case.metadata.get("expected_sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        return {}
    expected = {
        Path(source).name.casefold()
        for source in raw_sources
        if isinstance(source, str) and source.strip()
    }
    if not expected:
        return {}
    first_rank = next(
        (
            rank
            for rank, item in enumerate(response.items, 1)
            if _candidate_source_name(item.metadata) in expected
        ),
        None,
    )
    return {
        "source_hit_rate": 1.0 if first_rank is not None else 0.0,
        "source_mrr": 1.0 / first_rank if first_rank is not None else 0.0,
    }


def _candidate_source_name(metadata: dict[str, Any]) -> str:
    value = metadata.get("source_path") or metadata.get("source")
    return Path(value).name.casefold() if isinstance(value, str) else ""


__all__ = ["CustomEvaluator"]
