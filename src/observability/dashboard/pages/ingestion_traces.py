"""Browse ingestion history and timed pipeline stages."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from src.core.types import JsonDict
from src.observability.dashboard.services import ConfigService, TraceRecord, TraceService
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH

_INGESTION_STAGES = ("load", "split", "transform", "embed", "upsert")


def render(
    trace_service: TraceService | None = None,
    settings_path: str | Path | None = None,
) -> None:
    """Render newest-first ingestion traces and one run's stage timings."""
    st.title("Ingestion 追踪")
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

    snapshot = service.read_traces("ingestion")
    traces = list(snapshot.traces)
    _render_summary(traces)
    if snapshot.malformed_line_count:
        st.warning(f"已忽略 {snapshot.malformed_line_count} 条无效 Trace 记录。")
    if not traces:
        st.info("当前没有 Ingestion Trace。")
        return

    st.subheader("运行历史")
    st.dataframe(
        [_history_row(trace) for trace in traces],
        hide_index=True,
        width="stretch",
        column_config={
            "started_at": st.column_config.DatetimeColumn("Started", width="medium"),
            "source": st.column_config.TextColumn("Source"),
            "collection": st.column_config.TextColumn("Collection", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "total_ms": st.column_config.NumberColumn("Total (ms)", format="%.2f", width="small"),
        },
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


def _render_summary(traces: list[TraceRecord]) -> None:
    total, success, failed, skipped = st.columns(4, gap="medium")
    total.metric("Runs", len(traces))
    success.metric("Success", sum(trace.status == "success" for trace in traces))
    failed.metric("Failed", sum(trace.status == "failed" for trace in traces))
    skipped.metric("Skipped", sum(trace.status == "skipped" for trace in traces))


def _render_trace(trace: TraceRecord) -> None:
    st.subheader("运行详情")
    status, elapsed, chunks, images = st.columns(4, gap="medium")
    status.metric("Status", trace.status)
    elapsed.metric("Elapsed", f"{trace.total_elapsed_ms:.2f} ms")
    chunks.metric("Chunks", _result_count(trace, "chunk_count"))
    images.metric("Images", _result_count(trace, "image_count"))

    timed_stages = [
        stage
        for stage in trace.stages
        if stage.name in _INGESTION_STAGES and stage.elapsed_ms is not None
    ]
    if timed_stages:
        st.markdown("#### 阶段耗时")
        chart_rows = [
            {
                "stage": stage.name,
                "elapsed_ms": stage.elapsed_ms,
                "provider": stage.provider,
            }
            for stage in timed_stages
        ]
        st.bar_chart(
            chart_rows,
            x="elapsed_ms",
            y="stage",
            color="provider",
            horizontal=True,
            sort=False,
            x_label="Elapsed (ms)",
            y_label="Stage",
            height=max(260, len(chart_rows) * 54),
        )

    st.markdown("#### 阶段详情")
    for stage in trace.stages:
        if stage.name not in _INGESTION_STAGES:
            continue
        elapsed_label = (
            f"{stage.elapsed_ms:.2f} ms" if stage.elapsed_ms is not None else "not timed"
        )
        with st.expander(f"{stage.name} · {stage.method} / {stage.provider} · {elapsed_label}"):
            st.json(stage.details)


def _history_row(trace: TraceRecord) -> JsonDict:
    source_path = trace.metadata.get("source_path")
    source = Path(source_path).name if isinstance(source_path, str) else "unknown"
    return {
        "started_at": trace.started_at,
        "source": source,
        "collection": trace.metadata.get("collection", "unknown"),
        "status": trace.status,
        "total_ms": trace.total_elapsed_ms,
    }


def _trace_label(trace: TraceRecord) -> str:
    source = _history_row(trace)["source"]
    return f"{source} · {trace.status} · {trace.started_at}"


def _result_count(trace: TraceRecord, key: str) -> int:
    value = trace.metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    for stage in reversed(trace.stages):
        value = stage.details.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


__all__ = ["render"]
