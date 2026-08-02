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
                _render_component(component, selected_path)


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


def _render_component(component: ComponentSummary, settings_path: Path) -> None:
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
        if st.button(
            ":material/settings:",
            key=f"configure_{component.code}",
            help=f"配置 {component.label}",
            type="tertiary",
        ):
            _component_dialog(str(settings_path), component.code)


@st.dialog("组件配置", width="large")
def _component_dialog(settings_path: str, code: str) -> None:
    service = ConfigService.from_path(settings_path)
    component = next(item for item in service.component_summaries() if item.code == code)
    config = service.component_config(code)
    st.subheader(component.label)

    with st.form(f"component_form_{code}", border=False):
        values, api_key = _component_fields(code, config)
        submitted = st.form_submit_button(
            "保存配置",
            type="primary",
            icon=":material/save:",
            use_container_width=True,
        )
    if not submitted:
        return

    try:
        _validate_component_values(code, values)
        service.update_component(code, values, api_key=api_key)
        ConfigService.from_path(settings_path)
    except Exception as exc:
        st.error("配置保存失败")
        st.caption(f"{type(exc).__name__}: {exc}")
        return
    _load_runtime.clear()
    st.success("配置已保存")
    st.rerun()


def _component_fields(code: str, config: dict[str, object]) -> tuple[dict[str, object], str | None]:
    if code == "GEN":
        return _llm_fields(config)
    if code == "EMB":
        return _embedding_fields(config)
    if code == "SPLIT":
        return _splitter_fields(config), None
    if code == "RANK":
        return _reranker_fields(config), None
    if code == "STORE":
        return _store_fields(config), None
    if code == "EVAL":
        return _evaluation_fields(config), None
    raise ValueError(f"unknown dashboard component: {code}")


def _llm_fields(config: dict[str, object]) -> tuple[dict[str, object], str]:
    provider = _select_with_current(
        "Provider",
        ["openai", "deepseek", "ollama", "azure"],
        config.get("provider"),
        key="GEN_provider",
    )
    model = st.text_input("Model", value=_string(config.get("model")), key="GEN_model")
    base_url = st.text_input(
        "Base URL",
        value=_string(config.get("base_url")),
        key="GEN_base_url",
    )
    timeout = st.number_input(
        "Timeout (seconds)",
        min_value=1,
        value=_positive_int(config.get("timeout_seconds"), 60),
        key="GEN_timeout",
    )
    api_key = st.text_input(
        "API Key",
        type="password",
        placeholder=_secret_placeholder(config.get("api_key")),
        key="GEN_api_key",
    )
    return {
        "provider": provider,
        "model": model.strip(),
        "base_url": base_url.strip(),
        "timeout_seconds": int(timeout),
    }, api_key


def _embedding_fields(config: dict[str, object]) -> tuple[dict[str, object], str]:
    provider = _select_with_current(
        "Provider",
        ["minimax", "openai", "ollama", "hash", "azure"],
        config.get("provider"),
        key="EMB_provider",
    )
    model = st.text_input("Model", value=_string(config.get("model")), key="EMB_model")
    base_url = st.text_input(
        "Base URL",
        value=_string(config.get("base_url")),
        key="EMB_base_url",
    )
    first, second = st.columns(2)
    with first:
        group_id = st.text_input(
            "Group ID",
            value=_string(config.get("group_id")),
            key="EMB_group_id",
        )
        dimension = st.number_input(
            "Dimension",
            min_value=0,
            value=_non_negative_int(config.get("dimension"), 0),
            key="EMB_dimension",
        )
    with second:
        timeout = st.number_input(
            "Timeout (seconds)",
            min_value=1,
            value=_positive_int(config.get("timeout_seconds"), 30),
            key="EMB_timeout",
        )
        api_key = st.text_input(
            "API Key",
            type="password",
            placeholder=_secret_placeholder(config.get("api_key")),
            key="EMB_api_key",
        )

    values: dict[str, object] = {
        "provider": provider,
        "model": model.strip(),
        "base_url": base_url.strip(),
        "group_id": group_id.strip(),
        "timeout_seconds": int(timeout),
    }
    if dimension:
        values["dimension"] = int(dimension)
    return values, api_key


def _splitter_fields(config: dict[str, object]) -> dict[str, object]:
    provider = _select_with_current(
        "Provider", ["recursive"], config.get("provider"), key="SPLIT_provider"
    )
    chunk_size = st.number_input(
        "Chunk size",
        min_value=1,
        value=_positive_int(config.get("chunk_size"), 1000),
        key="SPLIT_chunk_size",
    )
    overlap = st.number_input(
        "Chunk overlap",
        min_value=0,
        value=_non_negative_int(config.get("chunk_overlap"), 200),
        key="SPLIT_overlap",
    )
    return {"provider": provider, "chunk_size": int(chunk_size), "chunk_overlap": int(overlap)}


def _reranker_fields(config: dict[str, object]) -> dict[str, object]:
    backend = _select_with_current(
        "Backend",
        ["none", "cross_encoder", "llm"],
        config.get("backend"),
        key="RANK_backend",
    )
    model = st.text_input("Model", value=_string(config.get("model")), key="RANK_model")
    top_m = st.number_input(
        "Top M",
        min_value=1,
        value=_positive_int(config.get("top_m"), 30),
        key="RANK_top_m",
    )
    timeout = st.number_input(
        "Timeout (seconds)",
        min_value=1,
        value=_positive_int(config.get("timeout_seconds"), 10),
        key="RANK_timeout",
    )
    return {
        "backend": backend,
        "model": model.strip(),
        "top_m": int(top_m),
        "timeout_seconds": int(timeout),
    }


def _store_fields(config: dict[str, object]) -> dict[str, object]:
    backend = _select_with_current(
        "Backend", ["chroma"], config.get("backend"), key="STORE_backend"
    )
    collection = st.text_input(
        "Collection",
        value=_string(config.get("collection_name")),
        key="STORE_collection",
    )
    persist_path = st.text_input(
        "Persist path",
        value=_string(config.get("persist_path")),
        key="STORE_path",
    )
    metric = _select_with_current(
        "Distance metric",
        ["cosine", "l2", "ip"],
        config.get("distance_metric"),
        key="STORE_metric",
    )
    return {
        "backend": backend,
        "collection_name": collection.strip(),
        "persist_path": persist_path.strip(),
        "distance_metric": metric,
    }


def _evaluation_fields(config: dict[str, object]) -> dict[str, object]:
    raw_backends = config.get("backends")
    selected = [str(item) for item in raw_backends] if isinstance(raw_backends, list) else []
    options = ["custom", "custom_metrics", "ragas"]
    options.extend(item for item in selected if item not in options)
    backends = st.multiselect(
        "Backends",
        options,
        default=selected,
        key="EVAL_backends",
    )
    golden_set = st.text_input(
        "Golden test set",
        value=_string(config.get("golden_test_set")),
        key="EVAL_golden",
    )
    return {"backends": backends or ["custom"], "golden_test_set": golden_set.strip()}


def _select_with_current(
    label: str,
    options: list[str],
    current: object,
    *,
    key: str,
) -> str:
    value = _string(current)
    choices = list(options)
    if value and value not in choices:
        choices.insert(0, value)
    index = choices.index(value) if value in choices else 0
    return st.selectbox(label, choices, index=index, key=key)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _non_negative_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


def _secret_placeholder(value: object) -> str:
    if isinstance(value, str) and value and not value.startswith("${"):
        return "已配置，留空保持不变"
    return "输入 API Key"


def _validate_component_values(code: str, values: dict[str, object]) -> None:
    if code == "GEN":
        if not _string(values.get("provider")).strip():
            raise ValueError("LLM provider must not be empty")
        if not _string(values.get("model")).strip():
            raise ValueError("LLM model must not be empty")
    elif code == "EMB":
        provider = _string(values.get("provider")).strip()
        if not provider:
            raise ValueError("Embedding provider must not be empty")
        if provider != "hash" and not _string(values.get("model")).strip():
            raise ValueError("Embedding model must not be empty")
    elif code == "SPLIT":
        chunk_size_value = values["chunk_size"]
        overlap_value = values["chunk_overlap"]
        if not isinstance(chunk_size_value, int) or not isinstance(overlap_value, int):
            raise ValueError("Chunk size and overlap must be integers")
        chunk_size = chunk_size_value
        overlap = overlap_value
        if overlap >= chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
    elif code == "STORE":
        if not _string(values.get("collection_name")).strip():
            raise ValueError("Vector store collection must not be empty")
        if not _string(values.get("persist_path")).strip():
            raise ValueError("Vector store persist path must not be empty")


__all__ = ["render"]
