"""Dashboard facade for configuring and running golden-set evaluations."""

from __future__ import annotations

from pathlib import Path

from src.core.services import build_local_query_engine
from src.core.settings import Settings
from src.core.types import EvaluationReport
from src.libs.evaluator import CompositeEvaluator, EvaluatorFactory
from src.observability.evaluation import EvalRunner
from src.ports.evaluation import BaseEvaluator
from src.ports.query import QueryEngine

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class EvaluationDashboardService:
    """Keep evaluator and query-engine assembly out of the API request handlers."""

    def __init__(self, settings: Settings, query_engine: QueryEngine) -> None:
        self.settings = settings
        self.query_engine = query_engine

    @classmethod
    def from_settings(cls, settings: Settings) -> EvaluationDashboardService:
        return cls(settings, build_local_query_engine(settings))

    def available_backends(self) -> list[str]:
        raw = self.settings.evaluation.get("backends")
        if not isinstance(raw, list):
            raise ValueError("evaluation.backends must be a list")
        return [item.strip().lower() for item in raw if isinstance(item, str) and item.strip()]

    def golden_test_sets(self) -> list[Path]:
        paths: set[Path] = set()
        configured = self.settings.evaluation.get("golden_test_set")
        if isinstance(configured, str) and configured.strip():
            paths.add(_project_path(configured))
        fixture_dir = PROJECT_ROOT / "tests" / "fixtures"
        paths.update(path.resolve() for path in fixture_dir.glob("*golden*.json"))
        return sorted(paths, key=lambda path: str(path).casefold())

    def run(self, test_set_path: str | Path, backends: list[str]) -> EvaluationReport:
        if not backends:
            raise ValueError("Select at least one evaluation backend")
        evaluators = [
            EvaluatorFactory.create(self.settings, backend=backend) for backend in backends
        ]
        evaluator: BaseEvaluator = (
            evaluators[0] if len(evaluators) == 1 else CompositeEvaluator(evaluators)
        )
        return EvalRunner(self.settings, self.query_engine, evaluator).run(test_set_path)


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


__all__ = ["EvaluationDashboardService"]
