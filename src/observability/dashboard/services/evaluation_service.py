"""Dashboard facade for configuring and running golden-set evaluations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.core.services import build_local_query_engine
from src.core.settings import Settings
from src.core.types import EvaluationReport, JsonDict
from src.libs.evaluator import CompositeEvaluator, EvaluatorFactory
from src.observability.evaluation import EvalRunner
from src.observability.logger import get_logger
from src.ports.evaluation import BaseEvaluator
from src.ports.query import QueryEngine

PROJECT_ROOT = Path(__file__).resolve().parents[4]
HOTPOTQA_ROOT = PROJECT_ROOT / "data" / "hotpotqa" / "benchmark"
BENCHMARK_TIMEOUT_SECONDS = 1800
logger = get_logger(__name__)


class EvaluationDashboardService:
    """Keep evaluator and query-engine assembly out of the API request handlers."""

    def __init__(
        self,
        settings: Settings,
        query_engine: QueryEngine,
        settings_path: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self.query_engine = query_engine
        self.settings_path = Path(settings_path).resolve() if settings_path is not None else None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        settings_path: str | Path | None = None,
    ) -> EvaluationDashboardService:
        return cls(settings, build_local_query_engine(settings), settings_path)

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

    def hotpotqa_summary(self) -> JsonDict:
        corpus = HOTPOTQA_ROOT / "corpus.jsonl"
        queries = HOTPOTQA_ROOT / "queries.jsonl"
        return {
            "dataset": "hotpotqa",
            "corpus_count": _line_count(corpus),
            "query_count": _line_count(queries),
            "available": corpus.is_file() and queries.is_file(),
        }

    def run_hotpotqa_benchmark(self, *, include_images: bool = True) -> JsonDict:
        if self.settings_path is None:
            raise RuntimeError("benchmark settings path is not available")
        if self.hotpotqa_summary()["available"] is not True:
            raise RuntimeError("HotpotQA benchmark data is not available")
        return _run_benchmark_process(self.settings_path, include_images=include_images)


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _run_benchmark_process(settings_path: Path, *, include_images: bool) -> JsonDict:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "evaluate_retrieval.py"),
        "--settings",
        str(settings_path),
        "--dataset",
        "hotpotqa",
    ]
    if not include_images:
        command.append("--skip-images")
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=BENCHMARK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("HotpotQA benchmark timed out") from exc
    if completed.returncode not in {0, 1}:
        logger.error("HotpotQA benchmark failed with exit code %s", completed.returncode)
        raise RuntimeError("HotpotQA benchmark execution failed")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("HotpotQA benchmark returned invalid output") from exc
    if not isinstance(report, dict):
        raise RuntimeError("HotpotQA benchmark returned invalid output")
    return report


__all__ = ["EvaluationDashboardService"]
