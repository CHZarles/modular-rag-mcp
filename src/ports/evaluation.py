"""评估模块端口契约。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import EvaluationCase, EvaluationReport, QueryResponse


@runtime_checkable
class BaseEvaluator(Protocol):
    """针对单条用例和响应计算一组指标。"""

    name: str

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]: ...


@runtime_checkable
class EvalRunner(Protocol):
    """运行评估用例并生成汇总报告。"""

    def run(
        self,
        cases: list[EvaluationCase],
        evaluators: list[BaseEvaluator],
    ) -> EvaluationReport: ...


class NoneEvaluator:
    """评估功能关闭时使用的空操作评估器。"""

    name = "none"

    def evaluate(
        self,
        case: EvaluationCase,
        response: QueryResponse,
        trace: Any | None = None,
    ) -> dict[str, float]:
        return {}
