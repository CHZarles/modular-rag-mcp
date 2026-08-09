"""Dashboard-facing view of runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.core.settings import (
    LOCAL_SECRETS_FILENAME,
    LOCAL_SETTINGS_FILENAME,
    ConfigSection,
    Settings,
    load_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_COMPONENT_SECTIONS = {
    "GEN": "llm",
    "EMB": "embedding",
    "SPLIT": "splitter",
    "RET": "retrieval",
    "RANK": "rerank",
    "STORE": "vector_store",
    "EVAL": "evaluation",
}
_TOGGLEABLE_COMPONENTS = {"GEN", "RANK"}
_EDITABLE_FIELDS = {
    "GEN": {"enabled", "provider", "model", "base_url", "endpoint", "timeout_seconds"},
    "EMB": {
        "provider",
        "model",
        "base_url",
        "group_id",
        "dimension",
        "endpoint",
        "deployment_name",
        "api_version",
        "timeout_seconds",
    },
    "SPLIT": {"provider", "chunk_size", "chunk_overlap"},
    "RET": {
        "enable_dense",
        "enable_sparse",
        "sparse_backend",
        "fusion_algorithm",
        "top_k_dense",
        "top_k_sparse",
        "top_k_final",
    },
    "RANK": {"enabled", "backend", "model", "top_m", "timeout_seconds"},
    "STORE": {"backend", "persist_path", "collection_name", "distance_metric"},
    "EVAL": {"backends", "golden_test_set"},
}
_SECRET_NAMES = {"GEN": "RAG_LLM_API_KEY", "EMB": "RAG_EMBEDDING_API_KEY"}
_DEFAULT_KNOWLEDGE_COLLECTION = "default"


@dataclass(frozen=True)
class ComponentSummary:
    """Safe, display-ready configuration for one pluggable component."""

    code: str
    label: str
    provider: str
    model: str | None = None
    details: tuple[tuple[str, str], ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class DashboardOptions:
    """Validated local server options used by the dashboard launcher."""

    enabled: bool
    address: str
    port: int
    traces_dir: Path
    auto_refresh: bool
    refresh_interval: int


class ConfigService:
    """Load Settings once and expose only values suitable for the dashboard."""

    def __init__(self, settings: Settings, settings_path: str | Path | None = None) -> None:
        self.settings = settings
        self.settings_path = Path(settings_path).expanduser() if settings_path else None

    @classmethod
    def from_path(cls, path: str | Path = DEFAULT_SETTINGS_PATH) -> ConfigService:
        settings_path = Path(path).expanduser()
        return cls(load_settings(str(settings_path)), settings_path)

    def component_config(self, code: str) -> ConfigSection:
        """Return a copy of one component's resolved runtime configuration."""
        section_name = _component_section(code)
        section = getattr(self.settings, section_name)
        return dict(section)

    def update_component(
        self,
        code: str,
        values: ConfigSection,
        *,
        api_key: str | None = None,
    ) -> None:
        """Persist dashboard edits as ignored local overrides without touching base YAML."""
        if self.settings_path is None:
            raise ValueError("component configuration requires a settings file path")
        normalized_code = code.strip().upper()
        section_name = _component_section(normalized_code)
        unknown = set(values) - _EDITABLE_FIELDS[normalized_code]
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unsupported {section_name} settings: {names}")

        updates = dict(values)
        clean_key = api_key.strip() if api_key is not None else ""
        if clean_key:
            secret_name = _SECRET_NAMES.get(normalized_code)
            if secret_name is None:
                raise ValueError(f"component {normalized_code} does not accept an API key")
            _merge_yaml_file(
                self.settings_path.with_name(LOCAL_SECRETS_FILENAME),
                {secret_name: clean_key},
                private=True,
            )
            updates["api_key"] = f"${{{secret_name}}}"

        _merge_yaml_file(
            self.settings_path.with_name(LOCAL_SETTINGS_FILENAME),
            {section_name: updates},
            private=False,
        )

    def set_component_enabled(self, code: str, enabled: bool) -> None:
        """Persist one component enable switch as a local override."""
        if self.settings_path is None:
            raise ValueError("component configuration requires a settings file path")
        normalized_code = code.strip().upper()
        if normalized_code not in _TOGGLEABLE_COMPONENTS:
            raise ValueError(f"component {normalized_code} must stay enabled")
        section_name = _component_section(normalized_code)
        updates: dict[str, Any] = {"enabled": enabled}
        if enabled and normalized_code == "RANK":
            backend = _text(getattr(self.settings, section_name), "backend", default="none")
            if backend.lower() in {"none", "disabled", "off"}:
                updates["backend"] = "cross_encoder"
        _merge_yaml_file(
            self.settings_path.with_name(LOCAL_SETTINGS_FILENAME),
            {section_name: updates},
            private=False,
        )

    def known_collections(self, indexed_collections: list[str] | tuple[str, ...] = ()) -> list[str]:
        """Return configured and already indexed knowledge collection names."""
        names = {
            _DEFAULT_KNOWLEDGE_COLLECTION,
            *_dashboard_collections(self.settings.dashboard),
            *_clean_collection_names(indexed_collections),
        }
        default_name = _optional_text(self.settings.vector_store, "collection_name")
        if default_name is not None and _is_simple_name(default_name):
            names.add(default_name)
        return sorted(names, key=str.casefold)

    def add_collection(self, name: str) -> None:
        """Persist an empty dashboard-visible knowledge collection name."""
        if self.settings_path is None:
            raise ValueError("collection creation requires a settings file path")
        cleaned = name.strip()
        if not _is_simple_name(cleaned):
            raise ValueError("collection must be a non-empty simple name")
        _merge_yaml_file(
            self.settings_path.with_name(LOCAL_SETTINGS_FILENAME),
            {"dashboard": {"collections": self.known_collections((cleaned,))}},
            private=False,
        )

    def component_summaries(self) -> tuple[ComponentSummary, ...]:
        settings = self.settings
        return (
            ComponentSummary(
                code="GEN",
                label="LLM",
                provider=_text(settings.llm, "provider"),
                model=_optional_text(settings.llm, "model"),
                enabled=_component_enabled(settings.llm, provider_key="provider"),
            ),
            ComponentSummary(
                code="EMB",
                label="Embedding",
                provider=_text(settings.embedding, "provider"),
                model=_optional_text(settings.embedding, "model"),
                details=(("Dimension", _display(settings.embedding.get("dimension"), "Auto")),),
                enabled=True,
            ),
            ComponentSummary(
                code="SPLIT",
                label="Splitter",
                provider=_text(settings.splitter, "provider"),
                details=(
                    ("Chunk size", _display(settings.splitter.get("chunk_size"))),
                    ("Overlap", _display(settings.splitter.get("chunk_overlap"))),
                ),
                enabled=True,
            ),
            ComponentSummary(
                code="RET",
                label="Retrieval",
                provider=_retrieval_mode(settings.retrieval),
                details=(
                    ("Dense top K", _display(settings.retrieval.get("top_k_dense"))),
                    ("Sparse top K", _display(settings.retrieval.get("top_k_sparse"))),
                    ("Final top K", _display(settings.retrieval.get("top_k_final"))),
                ),
                enabled=True,
            ),
            ComponentSummary(
                code="RANK",
                label="Reranker",
                provider=_text(settings.rerank, "backend"),
                model=_optional_text(settings.rerank, "model"),
                details=(("Top M", _display(settings.rerank.get("top_m"))),),
                enabled=_component_enabled(settings.rerank, provider_key="backend"),
            ),
            ComponentSummary(
                code="STORE",
                label="Vector store",
                provider=_text(settings.vector_store, "backend"),
                model=_optional_text(settings.vector_store, "collection_name"),
                details=(("Distance", _display(settings.vector_store.get("distance_metric"))),),
                enabled=True,
            ),
            ComponentSummary(
                code="EVAL",
                label="Evaluator",
                provider=_backend_list(settings.evaluation.get("backends")),
                details=(("Golden set", _path_name(settings.evaluation.get("golden_test_set"))),),
                enabled=True,
            ),
        )

    def dashboard_options(self) -> DashboardOptions:
        config = self.settings.dashboard
        return DashboardOptions(
            enabled=_boolean(config.get("enabled"), default=True),
            address=_text(config, "address", default="127.0.0.1"),
            port=_bounded_int(config.get("port"), default=8501, minimum=1, maximum=65535),
            traces_dir=_project_path(config.get("traces_dir"), default="./logs"),
            auto_refresh=_boolean(config.get("auto_refresh"), default=True),
            refresh_interval=_bounded_int(
                config.get("refresh_interval"),
                default=5,
                minimum=1,
                maximum=3600,
            ),
        )

    def trace_path(self) -> Path:
        """Return the legacy JSONL trace path (kept for backwards compat).

        Production no longer writes to this file — the SQLite Trace store
        owns the audit surface (plan §C2.2) — but downstream tooling may
        still reference the path when falling back to a local file.
        """
        return _project_path(
            self.settings.observability.get("log_file"),
            default="./logs/traces.jsonl",
        )

    def trace_db_path(self) -> Path:
        """Return the configured SQLite Trace DB path (plan §6.4)."""
        raw = self.settings.observability.get("trace_db_path")
        if isinstance(raw, str) and raw.strip():
            return _project_path(raw, default="./data/db/traces.db")
        return _project_path(
            self.settings.observability.get("trace_db_path"),
            default="./data/db/traces.db",
        )


def _text(config: ConfigSection, key: str, default: str = "Not configured") -> str:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _optional_text(config: ConfigSection, key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _display(value: Any, default: str = "Not configured") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _backend_list(value: Any) -> str:
    if isinstance(value, list):
        backends = [str(item).strip() for item in value if str(item).strip()]
        if backends:
            return ", ".join(backends)
    return "none"


def _retrieval_mode(config: ConfigSection) -> str:
    dense = config.get("enable_dense") is True
    sparse = config.get("enable_sparse") is True
    if dense and sparse:
        return "hybrid"
    if dense:
        return "dense"
    if sparse:
        return "bm25"
    return "disabled"


def _dashboard_collections(config: ConfigSection) -> list[str]:
    raw = config.get("collections")
    if not isinstance(raw, list):
        return []
    return _clean_collection_names(tuple(str(item) for item in raw))


def _clean_collection_names(values: tuple[str, ...] | list[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        cleaned = value.strip()
        if _is_simple_name(cleaned) and cleaned not in names:
            names.append(cleaned)
    return names


def _is_simple_name(value: str) -> bool:
    path = Path(value)
    return bool(value) and path.name == value and value not in {".", ".."}


def _component_enabled(
    config: ConfigSection,
    *,
    provider_key: str,
) -> bool:
    value = config.get("enabled")
    if isinstance(value, bool):
        if not value:
            return False
    if value is not None:
        normalized = str(value).strip().lower()
        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False

    provider = config.get(provider_key)
    normalized_provider = str(provider).strip().lower() if provider is not None else ""
    if not normalized_provider:
        return True
    return normalized_provider not in {"none", "disabled", "off"}


def _path_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "Not configured"
    return Path(value).name


def _boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"dashboard configuration error: invalid boolean {value!r}")


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("dashboard configuration error: expected an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("dashboard configuration error: expected an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"dashboard configuration error: integer must be between {minimum} and {maximum}"
        )
    return parsed


def _project_path(value: Any, *, default: str) -> Path:
    raw = str(value if value not in (None, "") else default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _component_section(code: str) -> str:
    normalized = code.strip().upper()
    try:
        return _COMPONENT_SECTIONS[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown dashboard component: {code}") from exc


def _merge_yaml_file(path: Path, updates: dict[str, Any], *, private: bool) -> None:
    current: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"local configuration must be a YAML mapping: {path}")
        current = loaded
    merged = _merge_mapping(current, updates)
    content = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    if private:
        temporary.chmod(0o600)
    os.replace(temporary, path)
    if private:
        path.chmod(0o600)


def _merge_mapping(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_mapping(current, value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "ComponentSummary",
    "ConfigService",
    "DEFAULT_SETTINGS_PATH",
    "DashboardOptions",
]
