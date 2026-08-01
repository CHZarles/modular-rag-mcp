"""System overview page for the local maintainer dashboard."""

from __future__ import annotations

import os
from html import escape
from pathlib import Path

import streamlit as st

from src.core.types import CollectionInfo
from src.libs.vector_store import ChromaStore
from src.observability.dashboard.services import ComponentSummary, ConfigService
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH


def render(settings_path: str | Path | None = None) -> None:
    """Render configuration and index health without exposing provider secrets."""
    configured_path = settings_path or os.environ.get(
        "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
    )
    selected_path = Path(configured_path).expanduser()
    try:
        modified_ns = selected_path.stat().st_mtime_ns
        config_service, vector_store = _load_runtime(str(selected_path), modified_ns)
    except Exception as exc:
        st.title("系统总览")
        st.error("配置加载失败")
        st.caption(f"{type(exc).__name__}: {exc}")
        return

    st.title("系统总览")
    st.caption("本地 RAG 运行状态")

    _render_asset_stats(vector_store)
    st.subheader("组件配置")
    components = config_service.component_summaries()
    for row_start in range(0, len(components), 3):
        columns = st.columns(3, gap="medium")
        for column, component in zip(columns, components[row_start : row_start + 3], strict=True):
            with column:
                _render_component(component)


@st.cache_resource(show_spinner=False)
def _load_runtime(settings_path: str, modified_ns: int) -> tuple[ConfigService, ChromaStore]:
    del modified_ns
    config_service = ConfigService.from_path(settings_path)
    settings = config_service.settings
    backend = str(settings.vector_store.get("backend", "")).strip().lower()
    if backend != "chroma":
        raise ValueError(f"Dashboard overview does not support vector backend {backend!r}")
    return config_service, ChromaStore(settings.vector_store)


def _render_asset_stats(vector_store: ChromaStore) -> None:
    st.subheader("数据资产")
    try:
        stats = vector_store.get_collection_stats()
    except Exception as exc:
        st.error("索引状态读取失败，请检查向量存储配置后刷新页面。")
        st.caption(f"{type(exc).__name__}: {exc}")
        stats = CollectionInfo(
            name=vector_store.collection_name,
            document_count=0,
            chunk_count=0,
            image_count=0,
        )

    collection, documents, chunks, images = st.columns(4, gap="medium")
    collection.metric("Collection", stats.name)
    documents.metric("Documents", stats.document_count)
    chunks.metric("Chunks", stats.chunk_count)
    images.metric("Images", stats.image_count)


def _render_component(component: ComponentSummary) -> None:
    state = "READY" if component.enabled else "OFF"
    state_class = "ready" if component.enabled else "off"
    with st.container(border=True):
        st.markdown(
            (
                '<div class="component-kicker">'
                f"<span>{escape(component.code)}</span>"
                f'<span class="component-state {state_class}">{state}</span>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown(f"**{component.label}**")
        primary = component.provider
        if component.model:
            primary = f"{primary} / {component.model}"
        st.code(primary, language=None)
        if component.details:
            st.caption(" · ".join(f"{label}: {value}" for label, value in component.details))


__all__ = ["render"]
