from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_evaluation_panel_runs_and_renders_metrics_and_cases() -> None:
    script = """\
from pathlib import Path
from src.core.types import EvaluationReport
from src.observability.dashboard.pages.evaluation_panel import render

class FakeEvaluationService:
    def available_backends(self):
        return ["custom"]
    def golden_test_sets(self):
        return [Path("golden.json")]
    def run(self, test_set_path, backends):
        return EvaluationReport(
            run_id="run-1",
            metrics={"hit_rate": 1.0, "mrr": 0.5},
            cases=[{
                "case_id": "q1",
                "query": "question",
                "expected_chunk_ids": ["gold"],
                "retrieved_chunk_ids": ["other", "gold"],
                "metrics": {"hit_rate": 1.0, "mrr": 0.5},
            }],
            metadata={"case_count": 1},
        )

render(FakeEvaluationService())
"""
    app = AppTest.from_string(script, default_timeout=20).run()

    app.button[0].click().run()

    assert not app.exception
    assert [title.value for title in app.title] == ["评估面板"]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("hit_rate", "1.0000"),
        ("mrr", "0.5000"),
    ]
    assert len(app.dataframe) == 1
