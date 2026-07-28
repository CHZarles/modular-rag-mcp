"""配置文件加载与校验。"""

from dataclasses import dataclass
from os.path import expandvars
from pathlib import Path
from typing import Any

import yaml

ConfigSection = dict[str, Any]

# 启动阶段只校验装配核心服务必需的字段，供应商专属字段由对应适配器负责。
_REQUIRED_FIELDS = {
    "knowledge_service": ("mode",),
    "llm": ("provider",),
    "embedding": ("provider",),
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
    vector_store: ConfigSection
    retrieval: ConfigSection
    rerank: ConfigSection
    evaluation: ConfigSection
    observability: ConfigSection


def validate_settings(settings: Settings) -> None:
    """必填配置缺失时抛出包含完整字段路径的错误。"""
    for section_name, field_names in _REQUIRED_FIELDS.items():
        section = getattr(settings, section_name)
        for field_name in field_names:
            value = section.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"Missing required setting: {section_name}.{field_name}")


def load_settings(path: str) -> Settings:
    """读取 YAML 配置，展开环境变量后完成基础校验。"""
    # 展开 ${MINIMAX_API_KEY} 一类引用，避免把密钥直接写入配置文件。
    content = expandvars(Path(path).read_text(encoding="utf-8"))
    raw = yaml.safe_load(content)
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

    settings = Settings(**sections)
    validate_settings(settings)
    return settings
