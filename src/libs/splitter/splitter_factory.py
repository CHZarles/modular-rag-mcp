"""按配置创建文本切分器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.ports.ingestion import BaseSplitter

SplitterProviderFactory = Callable[[Mapping[str, Any]], BaseSplitter]


class SplitterFactory:
    """轻量 provider 注册表；真实切分器实现由后续任务注册。"""

    _providers: dict[str, SplitterProviderFactory] = {}

    @classmethod
    def register(cls, provider: str, factory: SplitterProviderFactory) -> None:
        """注册一个 provider 名称到构造函数的映射。"""
        key = provider.strip().lower()
        if not key:
            raise ValueError("Splitter provider name must not be empty")
        cls._providers[key] = factory

    @classmethod
    def unregister(cls, provider: str) -> None:
        """测试或插件卸载时移除 provider。"""
        cls._providers.pop(provider.strip().lower(), None)

    @classmethod
    def create(cls, settings: Any) -> BaseSplitter:
        """从 Settings 或 splitter 配置字典创建对应的切分器。"""
        config = _splitter_config(settings)
        provider = str(config.get("provider", "")).strip().lower()
        if not provider:
            raise ValueError("Missing required setting: splitter.provider")

        factory = cls._providers.get(provider)
        if factory is None:
            available = ", ".join(sorted(cls._providers)) or "none"
            raise ValueError(
                f"Unsupported Splitter provider: {provider}. Registered providers: {available}"
            )
        return factory(config)


def create_splitter(settings: Any) -> BaseSplitter:
    """函数式入口，便于上层装配代码少写一个类名。"""
    return SplitterFactory.create(settings)


def _splitter_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("splitter", settings)
    else:
        config = getattr(settings, "splitter", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: splitter.provider")
    return config


__all__ = ["SplitterFactory", "SplitterProviderFactory", "create_splitter"]
