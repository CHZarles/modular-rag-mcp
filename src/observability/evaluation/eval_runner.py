"""基于评估端口的轻量评估执行器。"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from src.core.types import EvaluationCase, EvaluationReport, QueryResponse
from src.ports.evaluation import BaseEvaluator


class EvalRunner:
    """逐用例运行多个评估器，并计算每项指标的算术平均值。"""

    def __init__(
        self,
        responder: Callable[[EvaluationCase], QueryResponse] | None = None,
    ) -> None:
        self.responder = responder or _empty_response

    def run(
        self,
        cases: list[EvaluationCase],
        evaluators: list[BaseEvaluator],
    ) -> EvaluationReport:
        case_rows = []
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}

        for case in cases:
            response = self.responder(case)
            metrics: dict[str, float] = {}
            for evaluator in evaluators:
                for name, value in evaluator.evaluate(case, response).items():
                    metric_name = f"{evaluator.name}.{name}"
                    metrics[metric_name] = float(value)
                    totals[metric_name] = totals.get(metric_name, 0.0) + float(value)
                    counts[metric_name] = counts.get(metric_name, 0) + 1
            case_rows.append({"case_id": case.case_id, "metrics": metrics})

        # 只聚合实际由评估器返回的指标，允许不同评估器覆盖不同用例。
        aggregate = {
            name: totals[name] / counts[name]
            for name in sorted(totals)
            if counts[name] > 0
        }
        return EvaluationReport(
            run_id=str(uuid.uuid4()),
            metrics=aggregate,
            cases=case_rows,
        )


def _empty_response(case: EvaluationCase) -> QueryResponse:
    """未提供响应生成器时使用的空响应，便于独立测试评估框架。"""
    return QueryResponse(
        answer="",
        citations=[],
        items=[],
        request_id=case.case_id,
        metadata={"evaluation_case_id": case.case_id},
    )
