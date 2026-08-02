"""配置文件加载与校验。"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ConfigSection = dict[str, Any]

LOCAL_SETTINGS_FILENAME = "settings.local.yaml"
LOCAL_SECRETS_FILENAME = "secrets.local.yaml"
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 启动阶段只校验装配核心服务必需的字段，供应商专属字段由对应适配器负责。
_REQUIRED_FIELDS = {
    "knowledge_service": ("mode",),
    "llm": ("provider",),
    "embedding": ("provider",),
    "splitter": ("provider",),
    "vector_store": ("backend",),
    "retrieval": ("sparse_backend",),
    "rerank": ("backend",),
    "evaluation": ("backends",),
    "observability": ("enabled",),
}


@dataclass(frozen=True)
class Settings:
    """通过基础校验的顶层配置区段。"""

    knowledge_service: ConfigSection
    llm: ConfigSection
    embedding: ConfigSection
    splitter: ConfigSection
    vector_store: ConfigSection
    retrieval: ConfigSection
    rerank: ConfigSection
    evaluation: ConfigSection
    observability: ConfigSection
    ingestion: ConfigSection = field(default_factory=dict)
    vision_llm: ConfigSection | None = None
    dashboard: ConfigSection = field(default_factory=dict)


def validate_settings(settings: Settings) -> None:
    """必填配置缺失时抛出包含完整字段路径的错误。"""
    for section_name, field_names in _REQUIRED_FIELDS.items():
        section = getattr(settings, section_name)
        for field_name in field_names:
            value = section.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"Missing required setting: {section_name}.{field_name}")


def load_settings(path: str) -> Settings:
    """读取基础配置、本地覆盖和本地凭据，然后完成基础校验。"""
    settings_path = Path(path).expanduser()
    raw = _load_yaml_mapping(settings_path, label="settings")
    local_settings_path = settings_path.with_name(LOCAL_SETTINGS_FILENAME)
    if local_settings_path.exists():
        overrides = _load_yaml_mapping(local_settings_path, label="local settings")
        raw = _deep_merge(raw, overrides)

    secrets_path = settings_path.with_name(LOCAL_SECRETS_FILENAME)
    local_secrets = (
        _load_string_mapping(secrets_path, label="local secrets")
        if secrets_path.exists()
        else {}
    )
    raw = _expand_references(raw, local_secrets)
    if not isinstance(raw, dict):
        raise ValueError("settings must be a YAML mapping")

    # 这里只构建启动契约负责的区段；供应商细节稍后由具体适配器校验。
    sections: dict[str, ConfigSection] = {}
    for section_name, field_names in _REQUIRED_FIELDS.items():
        section = raw.get(section_name)
        if section is None:
            raise ValueError(f"Missing required setting: {section_name}.{field_names[0]}")
        if not isinstance(section, dict):
            raise ValueError(f"Setting {section_name} must be a mapping")
        sections[section_name] = section

    # ingestion 目前是可选区段，保持旧配置和直接构造 Settings 的调用兼容。
    ingestion = raw.get("ingestion", {})
    if not isinstance(ingestion, dict):
        raise ValueError("Setting ingestion must be a mapping")
    vision_llm = raw.get("vision_llm")
    if vision_llm is not None and not isinstance(vision_llm, dict):
        raise ValueError("Setting vision_llm must be a mapping")
    dashboard = raw.get("dashboard", {})
    if not isinstance(dashboard, dict):
        raise ValueError("Setting dashboard must be a mapping")
    settings = Settings(
        **sections,
        ingestion=ingestion,
        vision_llm=vision_llm,
        dashboard=dashboard,
    )
    validate_settings(settings)
    return settings


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a YAML mapping")
    return raw


def _load_string_mapping(path: Path, *, label: str) -> dict[str, str]:
    raw = _load_yaml_mapping(path, label=label)
    values: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{label} keys and values must be strings")
        values[key] = value
    return values


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _expand_references(value: Any, local_secrets: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_references(item, local_secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_references(item, local_secrets) for item in value]
    if not isinstance(value, str):
        return value

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        return os.environ.get(name, local_secrets.get(name, match.group(0)))

    return _ENV_REFERENCE.sub(replacement, value)


def resolve_settings_path(
    explicit_path: str | Path | None,
    *,
    default_path: str | Path,
) -> Path:
    """Resolve one process-wide settings path with explicit input taking precedence."""
    if explicit_path is not None:
        return Path(explicit_path).expanduser()

    environment_path = os.environ.get("RAG_SETTINGS_PATH", "").strip()
    if environment_path:
        return Path(environment_path).expanduser()
    return Path(default_path).expanduser()
