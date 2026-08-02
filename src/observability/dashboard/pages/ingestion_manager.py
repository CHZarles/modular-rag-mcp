"""Trigger local ingestion runs and coordinate document deletion."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

import streamlit as st

from src.core.settings import Settings
from src.core.trace import TraceCollector
from src.core.types import DocumentSummary, IngestionRequest, IngestionResult
from src.ingestion import build_ingestion_pipeline
from src.observability.dashboard.services import (
    ConfigService,
    DataService,
    IngestionJob,
    IngestionJobService,
)
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH
from src.observability.ingestion_trace import create_ingestion_trace_collector

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
_JOB_KEY = "ingestion_manager_job_id"


class UploadedPdf(Protocol):
    """The small UploadedFile surface needed by this page."""

    name: str

    def getvalue(self) -> bytes: ...


def render(
    data_service: DataService | None = None,
    settings: Settings | None = None,
    settings_path: str | Path | None = None,
    upload_root: str | Path | None = None,
    trace_collector: TraceCollector | None = None,
    job_service: IngestionJobService | None = None,
) -> None:
    """Render upload, progress and coordinated deletion controls."""
    st.title("Ingestion 管理")
    _render_notice()

    data = data_service
    loaded_settings = settings
    uploads = Path(upload_root).expanduser() if upload_root is not None else None
    collection_config: ConfigService | None = None
    if data is None or loaded_settings is None or uploads is None:
        configured_path = settings_path or os.environ.get(
            "RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)
        )
        selected_path = Path(configured_path).expanduser()
        try:
            (
                configured_config,
                configured_data,
                configured_settings,
                loaded_uploads,
                loaded_collector,
            ) = _load_services(str(selected_path), selected_path.stat().st_mtime_ns)
        except Exception as exc:
            st.error("摄取服务初始化失败，请检查运行配置。")
            st.caption(f"{type(exc).__name__}: {exc}")
            return
        collection_config = configured_config
        data = data or configured_data
        loaded_settings = loaded_settings or configured_settings
        uploads = uploads or loaded_uploads
        trace_collector = trace_collector or loaded_collector
    elif settings_path is not None:
        collection_config = ConfigService.from_path(settings_path)

    jobs = job_service or _load_job_service()
    ingest_tab, documents_tab = st.tabs(["摄取", "文档"])
    with ingest_tab:
        _render_ingestion(
            data,
            loaded_settings,
            uploads,
            trace_collector,
            jobs,
            collection_config,
        )
    with documents_tab:
        _render_deletion(data)


@st.cache_resource(show_spinner=False)
def _load_services(
    settings_path: str,
    modified_ns: int,
) -> tuple[ConfigService, DataService, Settings, Path, TraceCollector | None]:
    del modified_ns
    config = ConfigService.from_path(settings_path)
    storage = config.settings.ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")
    uploads = Path(_required_text(storage, "upload_root", "ingestion.storage")).expanduser()
    return (
        config,
        DataService.from_settings(config.settings),
        config.settings,
        uploads,
        create_ingestion_trace_collector(config.settings),
    )


@st.cache_resource(show_spinner=False)
def _load_job_service() -> IngestionJobService:
    return IngestionJobService(max_workers=1)


def _render_ingestion(
    data: DataService,
    settings: Settings,
    upload_root: Path,
    trace_collector: TraceCollector | None,
    jobs: IngestionJobService,
    collection_config: ConfigService | None = None,
) -> None:
    current_job = _current_job(jobs)
    if current_job is not None:
        _render_job_status(jobs, current_job.job_id)

    collections = data.list_collections()
    options = (
        collection_config.known_collections(collections)
        if collection_config is not None
        else _collection_options(settings, collections)
    )
    selection, created_collection = _render_collection_picker(
        options,
        collection_config,
    )
    if created_collection:
        return
    collection = selection
    uploaded = st.file_uploader("PDF 文件", type=["pdf"], key="ingestion_pdf")
    ai_enrichment = st.toggle(
        "AI 增强",
        value=_dashboard_ai_enrichment_default(settings),
        key="ingestion_ai_enrichment",
    )
    force = st.toggle("强制重新摄取", key="ingestion_force")
    can_ingest = (
        uploaded is not None
        and isinstance(collection, str)
        and bool(collection.strip())
        and (current_job is None or not current_job.active)
    )
    if not st.button(
        "开始摄取",
        type="primary",
        icon=":material/play_arrow:",
        disabled=not can_ingest,
    ):
        return

    assert uploaded is not None and isinstance(collection, str)
    try:
        source_path = _store_uploaded_pdf(uploaded, upload_root)
        ingestion_settings = _settings_for_ingestion_profile(
            settings,
            ai_enrichment=ai_enrichment,
        )
        job = jobs.submit(
            build_ingestion_pipeline(ingestion_settings),
            IngestionRequest(
                source_path=str(source_path),
                collection=collection.strip(),
                force=force,
            ),
            trace_collector,
        )
    except Exception as exc:
        st.error("摄取请求执行失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    st.session_state[_JOB_KEY] = job.job_id
    st.rerun()


def _render_collection_picker(
    options: list[str],
    collection_config: ConfigService | None,
) -> tuple[str, bool]:
    controls = st.columns([2, 2, 1], gap="small")
    with controls[0]:
        collection = st.selectbox(
            "Collection",
            options,
            accept_new_options=True,
            key="ingestion_collection",
        )
    created = False
    if collection_config is not None:
        with controls[1]:
            new_collection = st.text_input(
                "New collection",
                key="ingestion_new_collection",
            )
        with controls[2]:
            st.write("")
            create_clicked = st.button(
                ":material/add:",
                key="ingestion_create_collection",
                help="新建 Collection",
                type="tertiary",
            )
        if create_clicked:
            created = _create_collection(collection_config, new_collection)
            if created:
                st.session_state["ingestion_collection"] = new_collection.strip()
                _load_services.clear()
                st.rerun()
    return str(collection), created


def _create_collection(config: ConfigService, name: str) -> bool:
    try:
        config.add_collection(name)
    except Exception as exc:
        st.error("Collection 创建失败。")
        st.caption(f"{type(exc).__name__}: {exc}")
        return False
    return True


def _collection_options(settings: Settings, indexed_collections: list[str]) -> list[str]:
    names = {"default", *indexed_collections}
    configured = settings.vector_store.get("collection_name")
    if isinstance(configured, str) and configured.strip():
        names.add(configured.strip())
    dashboard_collections = settings.dashboard.get("collections")
    if isinstance(dashboard_collections, list):
        names.update(str(item).strip() for item in dashboard_collections if str(item).strip())
    return sorted(names, key=str.casefold)


def _current_job(jobs: IngestionJobService) -> IngestionJob | None:
    job_id = st.session_state.get(_JOB_KEY)
    if not isinstance(job_id, str):
        recovered = jobs.latest_active()
        if recovered is not None:
            st.session_state[_JOB_KEY] = recovered.job_id
        return recovered
    try:
        return jobs.get(job_id)
    except KeyError:
        st.session_state.pop(_JOB_KEY, None)
        return None


@st.fragment(run_every="1s")
def _render_job_status(jobs: IngestionJobService, job_id: str) -> None:
    try:
        job = jobs.get(job_id)
    except KeyError:
        st.warning("摄取任务状态已失效，请重新提交。")
        return

    if job.active:
        ratio = job.step / job.total if job.total > 0 else 0.0
        label = _STAGE_LABELS.get(job.stage, job.stage)
        st.progress(min(max(ratio, 0.0), 1.0), text=f"{label} · {job.step}/{job.total}")
        elapsed = int(time.time() - job.started_at) if job.started_at is not None else 0
        st.caption(f"{Path(job.source_path).name} · {elapsed}s")
        return
    if job.result is not None:
        _render_ingestion_result(job.result)
        return
    st.error(job.error or "摄取失败。")


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


def _settings_for_ingestion_profile(
    settings: Settings,
    *,
    ai_enrichment: bool,
) -> Settings:
    if ai_enrichment:
        return settings

    ingestion = dict(settings.ingestion)
    ingestion["chunk_refiner"] = {
        **_mapping(ingestion.get("chunk_refiner")),
        "use_llm": False,
    }
    ingestion["metadata_enricher"] = {
        **_mapping(ingestion.get("metadata_enricher")),
        "use_llm": False,
    }
    ingestion["image_captioner"] = {
        **_mapping(ingestion.get("image_captioner")),
        "enabled": False,
    }
    return replace(settings, ingestion=ingestion)


def _dashboard_ai_enrichment_default(settings: Settings) -> bool:
    value = settings.dashboard.get("ingestion_ai_enrichment_default", False)
    if not isinstance(value, bool):
        raise ValueError(
            "dashboard configuration error: ingestion_ai_enrichment_default must be boolean"
        )
    return value


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _document_label(document: DocumentSummary) -> str:
    collection = document.metadata.get("collection", "unknown")
    return f"{document.title or Path(document.source_path).name} · {collection}"


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


__all__ = ["render"]
