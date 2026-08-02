"""Browse query traces, retrieval routes and reranking changes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.observability.dashboard.services import (
    ConfigService,
    TraceRecord,
    TraceService,
    TraceStage,
)
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH

_QUERY_STAGES = (
    "query_processing",
    "dense_retrieval",
    "sparse_retrieval",
    "fusion",
    "metadata_filter",
    "rerank",
)


def render(
    trace_service: TraceService | None = None,
    settings_path: str | Path | None = None,
) -> None:
    st.title("Query 追踪")
    service = trace_service
    if service is None:
        configured_path = settings_path or os.environ.get(
            "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
        )
        selected_path = Path(configured_path).expanduser()
        try:
            service = _load_trace_service(str(selected_path), selected_path.stat().st_mtime_ns)
        except Exception as exc:
            st.error("Trace 服务初始化失败，请检查可观测性配置。")
            st.caption(f"{type(exc).__name__}: {exc}")
            return

    snapshot = service.read_traces("query")
    keyword = st.text_input("搜索 Query", icon=":material/search:").strip().casefold()
    traces = [
        trace
        for trace in snapshot.traces
        if not keyword or keyword in str(trace.metadata.get("query", "")).casefold()
    ]
    if snapshot.malformed_line_count:
        st.warning(f"已忽略 {snapshot.malformed_line_count} 条无效 Trace 记录。")
    if not traces:
        st.info("当前筛选范围内没有 Query Trace。")
        return

    st.dataframe(
        [_history_row(trace) for trace in traces],
        hide_index=True,
        width="stretch",
    )
    traces_by_id = {trace.trace_id: trace for trace in traces}
    selected_id = st.selectbox(
        "Trace",
        list(traces_by_id),
        format_func=lambda trace_id: _trace_label(traces_by_id[trace_id]),
    )
    _render_trace(traces_by_id[selected_id])


@st.cache_resource(show_spinner=False)
def _load_trace_service(settings_path: str, modified_ns: int) -> TraceService:
    del modified_ns
    config = ConfigService.from_path(settings_path)
    return TraceService(config.trace_path())


def _render_trace(trace: TraceRecord) -> None:
    status, elapsed, results = st.columns(3, gap="medium")
    status.metric("Status", trace.status)
    elapsed.metric("Elapsed", f"{trace.total_elapsed_ms:.2f} ms")
    results.metric("Results", _nonnegative_int(trace.metadata.get("result_count")))

    timed = [
        stage
        for stage in trace.stages
        if stage.name in _QUERY_STAGES and stage.elapsed_ms is not None
    ]
    if timed:
        st.markdown("#### 阶段耗时")
        st.bar_chart(
            [
                {
                    "stage": stage.name,
                    "elapsed_ms": stage.elapsed_ms,
                    "provider": stage.provider,
                }
                for stage in timed
            ],
            x="elapsed_ms",
            y="stage",
            color="provider",
            horizontal=True,
            sort=False,
            height=max(300, len(timed) * 50),
        )

    st.markdown("#### Dense / Sparse")
    dense_column, sparse_column = st.columns(2, gap="large")
    with dense_column:
        st.caption("Dense")
        st.dataframe(_stage_candidates(trace, "dense_retrieval"), hide_index=True, width="stretch")
    with sparse_column:
        st.caption("Sparse")
        st.dataframe(_stage_candidates(trace, "sparse_retrieval"), hide_index=True, width="stretch")

    rerank = _find_stage(trace, "rerank")
    if rerank is not None:
        st.markdown("#### Rerank 变化")
        st.dataframe(_rank_changes(rerank.details), hide_index=True, width="stretch")

    final_results = trace.metadata.get("results")
    if isinstance(final_results, list):
        st.markdown("#### 最终结果")
        st.dataframe(_candidate_rows(final_results), hide_index=True, width="stretch")


def _history_row(trace: TraceRecord) -> dict[str, Any]:
    return {
        "started_at": trace.started_at,
        "query": trace.metadata.get("query", "unknown"),
        "collection": trace.metadata.get("collection", "unknown"),
        "status": trace.status,
        "total_ms": trace.total_elapsed_ms,
    }


def _trace_label(trace: TraceRecord) -> str:
    return f"{trace.metadata.get('query', 'unknown')} · {trace.status} · {trace.started_at}"


def _find_stage(trace: TraceRecord, name: str) -> TraceStage | None:
    return next((stage for stage in reversed(trace.stages) if stage.name == name), None)


def _stage_candidates(trace: TraceRecord, name: str) -> list[dict[str, Any]]:
    stage = _find_stage(trace, name)
    return _candidate_rows(stage.details.get("candidates", []) if stage is not None else [])


def _candidate_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _rank_changes(details: dict[str, Any]) -> list[dict[str, Any]]:
    before = _candidate_rows(details.get("input_candidates"))
    after = _candidate_rows(details.get("output_candidates"))
    after_ranks = {str(item.get("chunk_id")): item.get("rank") for item in after}
    return [
        {
            "chunk_id": item.get("chunk_id"),
            "before": item.get("rank"),
            "after": after_ranks.get(str(item.get("chunk_id"))),
            "change": _rank_delta(item.get("rank"), after_ranks.get(str(item.get("chunk_id")))),
        }
        for item in before
    ]


def _rank_delta(before: Any, after: Any) -> int | None:
    if isinstance(before, int) and isinstance(after, int):
        return before - after
    return None


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


__all__ = ["render"]
