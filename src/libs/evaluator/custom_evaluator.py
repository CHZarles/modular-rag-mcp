"""无需外部服务的检索质量指标。"""

from __future__ import annotations

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
        golden_ids = set(case.expected_chunk_ids)
        if not golden_ids:
            return {"hit_rate": 0.0, "mrr": 0.0}

        first_relevant_rank = next(
            (
                rank
                for rank, item in enumerate(response.items, start=1)
                if item.chunk_id in golden_ids
            ),
            None,
        )
        if first_relevant_rank is None:
            return {"hit_rate": 0.0, "mrr": 0.0}
        return {"hit_rate": 1.0, "mrr": 1.0 / first_relevant_rank}


__all__ = ["CustomEvaluator"]
