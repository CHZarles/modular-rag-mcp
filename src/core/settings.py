"""Settings: load + validate ``config/settings.yaml``.

A3 scope: structural loading and minimum validation only. No network calls,
no factory instantiation. Provider instantiation belongs to the Libs layer
(stages B–E). This module's job is to be the single source of truth for
*what the YAML says*, with friendly errors when required fields are missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SettingsError(ValueError):
    """Raised when settings fail to load or fail validation.

    The message always includes the dotted path to the offending field so
    users can fix the YAML directly without grepping.
    """


# ---------------------------------------------------------------------------
# Nested config sections — one dataclass per top-level block.
# Frozen=True keeps the loaded settings immutable, so adapters cannot
# accidentally mutate shared state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeServiceConfig:
    mode: str
    endpoint: str | None = None
    timeout_seconds: int = 30


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str | None = None
    azure_endpoint: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str | None = None
    api_key: str | None = None
    azure_endpoint: str | None = None


@dataclass(frozen=True)
class VisionLLMConfig:
    provider: str
    model: str | None = None
    azure_endpoint: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class VectorStoreConfig:
    backend: str
    persist_path: str = "./data/db/chroma"


@dataclass(frozen=True)
class RetrievalConfig:
    sparse_backend: str
    fusion_algorithm: str = "rrf"
    top_k_dense: int = 20
    top_k_sparse: int = 20
    top_k_final: int = 10


@dataclass(frozen=True)
class RerankConfig:
    backend: str
    model: str | None = None
    top_m: int = 30


@dataclass(frozen=True)
class EvaluationConfig:
    backends: list[str] = field(default_factory=list)
    golden_test_set: str = "./tests/fixtures/golden_test_set.json"


@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool = True
    log_file: str = "./logs/traces.jsonl"


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = True
    port: int = 8501
    traces_dir: str = "./logs"
    auto_refresh: bool = True
    refresh_interval: int = 5


@dataclass(frozen=True)
class Settings:
    knowledge_service: KnowledgeServiceConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    vision_llm: VisionLLMConfig
    vector_store: VectorStoreConfig
    retrieval: RetrievalConfig
    rerank: RerankConfig
    evaluation: EvaluationConfig
    observability: ObservabilityConfig
    dashboard: DashboardConfig


# ---------------------------------------------------------------------------
# Required-field table — drives pre-instantiation validation.
# Tuple: (dotted path, section key, leaf name).
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS: list[tuple[str, str, str]] = [
    ("knowledge_service.mode", "knowledge_service", "mode"),
    ("llm.provider", "llm", "provider"),
    ("embedding.provider", "embedding", "provider"),
    ("vision_llm.provider", "vision_llm", "provider"),
    ("vector_store.backend", "vector_store", "backend"),
    ("retrieval.sparse_backend", "retrieval", "sparse_backend"),
    ("rerank.backend", "rerank", "backend"),
]


# Sections that have no required field but still need parsing.
_ALL_SECTIONS: dict[str, type] = {
    "knowledge_service": KnowledgeServiceConfig,
    "llm": LLMConfig,
    "embedding": EmbeddingConfig,
    "vision_llm": VisionLLMConfig,
    "vector_store": VectorStoreConfig,
    "retrieval": RetrievalConfig,
    "rerank": RerankConfig,
    "evaluation": EvaluationConfig,
    "observability": ObservabilityConfig,
    "dashboard": DashboardConfig,
}


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any, field_path: str) -> int:
    if isinstance(value, bool):  # bool is a subclass of int; reject explicitly
        raise SettingsError(f"{field_path}: expected int, got bool")
    if not isinstance(value, int):
        raise SettingsError(f"{field_path}: expected int, got {type(value).__name__}")
    return value


def _coerce_str_or_none(value: Any, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsError(f"{field_path}: expected str or null, got {type(value).__name__}")
    return value


def _parse_section(raw: dict[str, Any], cls: type, section_name: str) -> Any:
    """Instantiate ``cls`` from ``raw``, normalising missing optionals."""
    kwargs: dict[str, Any] = {}
    for fld in cls.__dataclass_fields__:  # type: ignore[attr-defined]
        if fld in raw:
            value = raw[fld]
            fld_type = cls.__dataclass_fields__[fld].type  # type: ignore[attr-defined]
            if fld_type in ("int", int):
                value = _coerce_int(value, f"{section_name}.{fld}")
            elif fld_type in ("str | None", "Optional[str]"):
                value = _coerce_str_or_none(value, f"{section_name}.{fld}")
            kwargs[fld] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise SettingsError(f"{section_name}: {exc}") from exc


def _check_required_in_raw(raw: dict[str, Any]) -> None:
    """Raise SettingsError if any required field is missing or blank in raw."""
    missing: list[str] = []
    for dotted_path, section_key, leaf in _REQUIRED_FIELDS:
        section_raw = raw.get(section_key)
        if not isinstance(section_raw, dict):
            missing.append(dotted_path)
            continue
        if section_raw.get(leaf) in (None, ""):
            missing.append(dotted_path)
    if missing:
        raise SettingsError(
            "missing required fields: " + ", ".join(sorted(missing))
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_settings(path: str | Path) -> Settings:
    """Read YAML at ``path`` and return a validated :class:`Settings`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        SettingsError: If the file is not parseable YAML, the top level is
            not a mapping, or any required field is missing.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"settings file not found: {p}")

    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise SettingsError(f"invalid YAML in {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SettingsError(
            f"{p}: top-level YAML must be a mapping, got {type(raw).__name__}"
        )

    # Validate required fields first so error messages name the dotted path
    # even when a section is missing entirely or its required leaf is blank.
    _check_required_in_raw(raw)

    parsed: dict[str, Any] = {}
    for section_name, cls in _ALL_SECTIONS.items():
        section_raw = raw.get(section_name)
        if section_raw is None:
            parsed[section_name] = cls()
            continue
        if not isinstance(section_raw, dict):
            raise SettingsError(
                f"{section_name}: expected mapping, got {type(section_raw).__name__}"
            )
        parsed[section_name] = _parse_section(section_raw, cls, section_name)

    settings = Settings(**parsed)
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    """Check mandatory fields and numeric sanity. Raises :class:`SettingsError`
    with a dotted-path message if anything is missing or malformed.
    """
    section_map: dict[str, Any] = {
        "knowledge_service": settings.knowledge_service,
        "llm": settings.llm,
        "embedding": settings.embedding,
        "vision_llm": settings.vision_llm,
        "vector_store": settings.vector_store,
        "retrieval": settings.retrieval,
        "rerank": settings.rerank,
    }
    missing: list[str] = []
    for dotted_path, section_attr, leaf in [
        (path, attr, leaf) for path, attr, leaf in _REQUIRED_FIELDS
    ]:
        section = section_map[section_attr]
        value = getattr(section, leaf, None)
        if value is None or value == "":
            missing.append(dotted_path)

    if missing:
        raise SettingsError(
            "missing required fields: " + ", ".join(sorted(missing))
        )

    if settings.retrieval.top_k_dense <= 0:
        raise SettingsError("retrieval.top_k_dense: must be > 0")
    if settings.retrieval.top_k_sparse <= 0:
        raise SettingsError("retrieval.top_k_sparse: must be > 0")
    if settings.retrieval.top_k_final <= 0:
        raise SettingsError("retrieval.top_k_final: must be > 0")