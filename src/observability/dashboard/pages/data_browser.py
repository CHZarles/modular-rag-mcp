"""Browse active documents, chunks and image references."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.core.types import DocumentSummary, JsonDict
from src.observability.dashboard.services import ConfigService, DataService
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH


def render(
    data_service: DataService | None = None,
    settings_path: str | Path | None = None,
) -> None:
    """Render an active-generation-only view of the local knowledge assets."""
    st.title("数据浏览器")
    service = data_service
    if service is None:
        configured_path = settings_path or os.environ.get(
            "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
        )
        selected_path = Path(configured_path).expanduser()
        try:
            service = _load_data_service(str(selected_path), selected_path.stat().st_mtime_ns)
        except Exception as exc:
            st.error("数据服务初始化失败，请检查摄取存储配置。")
            st.caption(f"{type(exc).__name__}: {exc}")
            return

    collections = service.list_collections()
    selected_label = st.selectbox("Collection", ["全部", *collections])
    selected_collection = None if selected_label == "全部" else selected_label
    documents = service.list_documents(selected_collection)
    stats = service.get_collection_stats(selected_collection)
    _render_stats(stats.document_count, stats.chunk_count, stats.image_count)

    if not documents:
        st.info("当前筛选范围内没有已摄取文档。")
        return

    st.subheader("文档")
    st.dataframe(
        [_document_row(document) for document in documents],
        width="stretch",
        hide_index=True,
        column_config={
            "source_path": st.column_config.TextColumn("Source"),
            "collection": st.column_config.TextColumn("Collection", width="small"),
            "chunk_count": st.column_config.NumberColumn("Chunks", width="small"),
            "image_count": st.column_config.NumberColumn("Images", width="small"),
            "processed_at": st.column_config.DatetimeColumn("Ingested", width="medium"),
        },
    )

    documents_by_id = {document.doc_id: document for document in documents}
    selected_doc_id = st.selectbox(
        "Document",
        list(documents_by_id),
        index=None,
        placeholder="选择文档",
        format_func=lambda doc_id: _document_label(documents_by_id[doc_id]),
    )
    if selected_doc_id is None:
        return

    try:
        detail = service.get_document_detail(selected_doc_id)
    except Exception as exc:
        st.error("文档详情读取失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    _render_detail(detail)


@st.cache_resource(show_spinner=False)
def _load_data_service(settings_path: str, modified_ns: int) -> DataService:
    del modified_ns
    config = ConfigService.from_path(settings_path)
    return DataService.from_settings(config.settings)


def _render_stats(document_count: int, chunk_count: int, image_count: int) -> None:
    documents, chunks, images = st.columns(3, gap="medium")
    documents.metric("Documents", document_count)
    chunks.metric("Chunks", chunk_count)
    images.metric("Images", image_count)


def _document_row(document: DocumentSummary) -> JsonDict:
    return {
        "source_path": document.source_path,
        "collection": document.metadata.get("collection"),
        "chunk_count": document.metadata.get("chunk_count", 0),
        "image_count": document.metadata.get("image_count", 0),
        "processed_at": document.metadata.get("processed_at"),
    }


def _document_label(document: DocumentSummary) -> str:
    return f"{document.title or Path(document.source_path).name} · {document.metadata.get('collection')}"


def _render_detail(detail: JsonDict) -> None:
    document = detail.get("document")
    chunks = detail.get("chunks")
    images = detail.get("images")
    if (
        not isinstance(document, dict)
        or not isinstance(chunks, list)
        or not isinstance(images, list)
    ):
        st.error("文档详情格式无效。")
        return

    st.subheader(str(document.get("title") or Path(str(document.get("source_path", ""))).name))
    st.caption(str(document.get("source_path", "")))
    st.markdown("#### Chunks")
    for position, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata")
        page = metadata.get("page") if isinstance(metadata, dict) else None
        suffix = f" · Page {page}" if page is not None else ""
        with st.expander(f"Chunk {position:03d}{suffix}"):
            st.text(str(chunk.get("text", "")))
            st.json(metadata if isinstance(metadata, dict) else {})

    if not images:
        return
    st.markdown("#### Images")
    columns = st.columns(3, gap="medium")
    for index, image in enumerate(images):
        if not isinstance(image, dict):
            continue
        with columns[index % len(columns)]:
            _render_image(image)


def _render_image(image: dict[str, Any]) -> None:
    image_id = str(image.get("image_id", "image"))
    raw_path = image.get("path")
    path = Path(raw_path) if isinstance(raw_path, str) else None
    if path is not None and path.is_file():
        st.image(str(path), caption=image_id, width="stretch")
    else:
        st.warning(f"图片文件不可用：{image_id}")


__all__ = ["render"]
