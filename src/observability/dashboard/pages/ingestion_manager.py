"""Trigger local ingestion runs and coordinate document deletion."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

import streamlit as st

from src.application.services import IngestionService
from src.core.types import DocumentSummary, IngestionRequest, IngestionResult
from src.ingestion import build_ingestion_pipeline
from src.observability.dashboard.services import ConfigService, DataService
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH

_STAGE_LABELS = {
    "integrity": "校验文档",
    "load": "加载 PDF",
    "split": "切分内容",
    "transform": "增强 Chunk",
    "encode": "生成向量",
    "store": "写入索引",
    "complete": "完成",
}
_NOTICE_KEY = "ingestion_manager_notice"


class UploadedPdf(Protocol):
    """The small UploadedFile surface needed by this page."""

    name: str

    def getvalue(self) -> bytes: ...


def render(
    data_service: DataService | None = None,
    ingestion_service: IngestionService | None = None,
    settings_path: str | Path | None = None,
    upload_root: str | Path | None = None,
) -> None:
    """Render upload, progress and coordinated deletion controls."""
    st.title("Ingestion 管理")
    _render_notice()

    data = data_service
    ingestion = ingestion_service
    uploads = Path(upload_root).expanduser() if upload_root is not None else None
    if data is None or ingestion is None or uploads is None:
        configured_path = settings_path or os.environ.get(
            "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
        )
        selected_path = Path(configured_path).expanduser()
        try:
            loaded_data, loaded_ingestion, loaded_uploads = _load_services(
                str(selected_path), selected_path.stat().st_mtime_ns
            )
        except Exception as exc:
            st.error("摄取服务初始化失败，请检查运行配置。")
            st.caption(f"{type(exc).__name__}: {exc}")
            return
        data = data or loaded_data
        ingestion = ingestion or loaded_ingestion
        uploads = uploads or loaded_uploads

    ingest_tab, documents_tab = st.tabs(["摄取", "文档"])
    with ingest_tab:
        _render_ingestion(data, ingestion, uploads)
    with documents_tab:
        _render_deletion(data)


@st.cache_resource(show_spinner=False)
def _load_services(
    settings_path: str,
    modified_ns: int,
) -> tuple[DataService, IngestionService, Path]:
    del modified_ns
    config = ConfigService.from_path(settings_path)
    storage = config.settings.ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")
    uploads = Path(_required_text(storage, "upload_root", "ingestion.storage")).expanduser()
    return (
        DataService.from_settings(config.settings),
        build_ingestion_pipeline(config.settings),
        uploads,
    )


def _render_ingestion(
    data: DataService,
    ingestion: IngestionService,
    upload_root: Path,
) -> None:
    collections = data.list_collections()
    options = collections or ["default"]
    collection = st.selectbox(
        "Collection",
        options,
        accept_new_options=True,
        key="ingestion_collection",
    )
    uploaded = st.file_uploader("PDF 文件", type=["pdf"], key="ingestion_pdf")
    force = st.toggle("强制重新摄取", key="ingestion_force")
    can_ingest = uploaded is not None and isinstance(collection, str) and bool(collection.strip())
    if not st.button(
        "开始摄取",
        type="primary",
        icon=":material/play_arrow:",
        disabled=not can_ingest,
    ):
        return

    assert uploaded is not None and isinstance(collection, str)
    progress = st.progress(0, text="准备摄取")

    def update_progress(stage: str, step: int, total: int) -> None:
        label = _STAGE_LABELS.get(stage, stage)
        progress.progress(step / total, text=f"{label} · {step}/{total}")

    try:
        result = _ingest_uploaded_pdf(
            uploaded,
            collection.strip(),
            force,
            ingestion,
            update_progress,
            upload_root,
        )
    except Exception as exc:
        progress.empty()
        st.error("摄取请求执行失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    _render_ingestion_result(result)


def _render_deletion(data: DataService) -> None:
    documents = data.list_documents()
    if not documents:
        st.info("当前没有可管理的文档。")
        return

    documents_by_id = {document.doc_id: document for document in documents}
    selected_id = st.selectbox(
        "Document",
        list(documents_by_id),
        format_func=lambda doc_id: _document_label(documents_by_id[doc_id]),
        key="delete_document_id",
    )
    selected = documents_by_id[selected_id]
    st.code(selected.source_path, language=None)
    confirmed = st.checkbox("确认删除该文档及其索引数据", key="confirm_document_delete")
    if not st.button(
        "删除文档",
        icon=":material/delete:",
        disabled=not confirmed,
    ):
        return

    collection = selected.metadata.get("collection")
    if not isinstance(collection, str):
        st.error("文档缺少有效的 Collection。")
        return
    try:
        result = data.delete_document(selected.source_path, collection)
    except Exception as exc:
        st.error("文档删除失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    if result.errors:
        st.error("文档未能从所有存储中完整删除。")
        st.json(result.to_dict())
        return

    st.session_state[_NOTICE_KEY] = f"已删除 {selected.title or Path(selected.source_path).name}"
    st.rerun()


def _render_notice() -> None:
    notice = st.session_state.pop(_NOTICE_KEY, None)
    if isinstance(notice, str):
        st.success(notice)


def _render_ingestion_result(result: IngestionResult) -> None:
    if result.status == "success":
        st.success(f"摄取完成：{result.chunk_count} Chunks，{result.image_count} Images")
    elif result.status == "skipped":
        st.info("文档内容未变化，已跳过。")
    else:
        st.error(result.error or "摄取失败。")


def _ingest_uploaded_pdf(
    uploaded: UploadedPdf,
    collection: str,
    force: bool,
    ingestion: IngestionService,
    on_progress: Callable[[str, int, int], None],
    upload_root: Path,
) -> IngestionResult:
    source_path = _store_uploaded_pdf(uploaded, upload_root)
    return ingestion.ingest(
        IngestionRequest(
            source_path=str(source_path),
            collection=collection,
            force=force,
        ),
        on_progress=on_progress,
    )


def _store_uploaded_pdf(uploaded: UploadedPdf, upload_root: Path) -> Path:
    filename = Path(uploaded.name).name
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("只支持 PDF 文件")
    content = uploaded.getvalue()
    if not content:
        raise ValueError("PDF 文件不能为空")
    upload_root.mkdir(parents=True, exist_ok=True)
    path = (upload_root / filename).resolve()
    with NamedTemporaryFile(
        dir=upload_root,
        prefix=f".{filename}.",
        suffix=".uploading",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def _document_label(document: DocumentSummary) -> str:
    collection = document.metadata.get("collection", "unknown")
    return f"{document.title or Path(document.source_path).name} · {collection}"


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


__all__ = ["render"]
