"""Run the configured evaluators against a local golden test set."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.services import build_local_query_engine  # noqa: E402
from src.core.settings import load_settings, resolve_settings_path  # noqa: E402
from src.libs.evaluator import create_evaluator  # noqa: E402
from src.observability.evaluation import EvalRunner  # noqa: E402

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_path: str | Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Run the local RAG golden-set evaluation")
    parser.add_argument("--test-set", default=None, help="Golden test set JSON path")
    args = parser.parse_args(argv)
    try:
        selected_settings_path = resolve_settings_path(
            settings_path,
            default_path=DEFAULT_SETTINGS_PATH,
        )
        settings = load_settings(str(selected_settings_path))
        configured_path = args.test_set or settings.evaluation.get("golden_test_set")
        if not isinstance(configured_path, str) or not configured_path.strip():
            raise ValueError("Missing required setting: evaluation.golden_test_set")
        runner = EvalRunner(
            settings,
            build_local_query_engine(settings),
            create_evaluator(settings),
        )
        report = runner.run(configured_path)
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
