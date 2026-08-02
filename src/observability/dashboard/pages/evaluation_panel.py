"""Run configured evaluators and inspect golden-set results."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.core.types import EvaluationReport
from src.observability.dashboard.services import ConfigService, EvaluationDashboardService
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH


def render(
    evaluation_service: EvaluationDashboardService | None = None,
    settings_path: str | Path | None = None,
) -> None:
    st.title("评估面板")
    service = evaluation_service
    if service is None:
        configured_path = settings_path or os.environ.get(
            "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
        )
        settings_file = Path(configured_path).expanduser()
        try:
            service = _load_evaluation_service(str(settings_file), settings_file.stat().st_mtime_ns)
        except Exception as exc:
            st.error("评估服务初始化失败，请检查检索与评估配置。")
            st.caption(f"{type(exc).__name__}: {exc}")
            return

    available = service.available_backends()
    selected_backends = st.multiselect(
        "Evaluator",
        available,
        default=available,
        key="evaluation_backends",
    )
    test_sets = service.golden_test_sets()
    selected_test_set = st.selectbox(
        "Golden test set",
        [str(path) for path in test_sets],
        accept_new_options=True,
        index=0 if test_sets else None,
        placeholder="选择 Golden Test Set",
    )
    can_run = (
        bool(selected_backends) and isinstance(selected_test_set, str) and bool(selected_test_set)
    )
    if not st.button(
        "运行评估",
        type="primary",
        icon=":material/play_arrow:",
        disabled=not can_run,
    ):
        return

    assert isinstance(selected_test_set, str)
    try:
        with st.spinner("正在运行评估…"):
            report = service.run(selected_test_set, selected_backends)
    except Exception as exc:
        st.error("评估运行失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    _render_report(report)


@st.cache_resource(show_spinner=False)
def _load_evaluation_service(
    settings_path: str,
    modified_ns: int,
) -> EvaluationDashboardService:
    del modified_ns
    config = ConfigService.from_path(settings_path)
    return EvaluationDashboardService.from_settings(config.settings)


def _render_report(report: EvaluationReport) -> None:
    st.subheader("指标")
    metrics = list(report.metrics.items())
    for offset in range(0, len(metrics), 4):
        batch = metrics[offset : offset + 4]
        columns = st.columns(len(batch), gap="medium")
        for column, (name, value) in zip(columns, batch, strict=True):
            column.metric(name, f"{value:.4f}")

    st.subheader("用例明细")
    st.dataframe(
        [_case_row(case) for case in report.cases],
        hide_index=True,
        width="stretch",
    )
    with st.expander("运行信息"):
        st.json({"run_id": report.run_id, **report.metadata})


def _case_row(case: dict[str, Any]) -> dict[str, Any]:
    metrics = case.get("metrics")
    row = {
        "case_id": case.get("case_id"),
        "query": case.get("query"),
        "expected": ", ".join(str(item) for item in case.get("expected_chunk_ids", [])),
        "retrieved": ", ".join(str(item) for item in case.get("retrieved_chunk_ids", [])),
    }
    if isinstance(metrics, dict):
        row.update(metrics)
    return row


__all__ = ["render"]
