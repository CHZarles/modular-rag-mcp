"""按配置创建 Embedding 客户端。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.ports.ingestion import BaseEmbedding

EmbeddingProviderFactory = Callable[[Mapping[str, Any]], BaseEmbedding]


class EmbeddingFactory:
    """轻量 provider 注册表；内置供应商按需延迟注册。"""

    _providers: dict[str, EmbeddingProviderFactory] = {}

    @classmethod
    def register(cls, provider: str, factory: EmbeddingProviderFactory) -> None:
        """注册一个 provider 名称到构造函数的映射。"""
        key = provider.strip().lower()
        if not key:
            raise ValueError("Embedding provider name must not be empty")
        cls._providers[key] = factory

    @classmethod
    def available_providers(cls) -> list[str]:
        """已注册的 Provider 列表，供配置 UI 展示。"""
        _register_default_providers()
        return sorted(cls._providers)

    @classmethod
    def unregister(cls, provider: str) -> None:
        """测试或插件卸载时移除 provider。"""
        cls._providers.pop(provider.strip().lower(), None)

    @classmethod
    def create(cls, settings: Any) -> BaseEmbedding:
        """从 Settings 或 embedding 配置字典创建对应的 Embedding 客户端。"""
        _register_default_providers()
        config = _embedding_config(settings)
        provider = str(config.get("provider", "")).strip().lower()
        if not provider:
            raise ValueError("Missing required setting: embedding.provider")

        factory = cls._providers.get(provider)
        if factory is None:
            available = ", ".join(sorted(cls._providers)) or "none"
            raise ValueError(
                f"Unsupported Embedding provider: {provider}. Registered providers: {available}"
            )
        return factory(config)


def create_embedding(settings: Any) -> BaseEmbedding:
    """函数式入口，便于上层装配代码少写一个类名。"""
    return EmbeddingFactory.create(settings)


def _embedding_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("embedding", settings)
    else:
        config = getattr(settings, "embedding", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: embedding.provider")
    return config


_DEFAULT_PROVIDERS_REGISTERED = False


def _register_default_providers() -> None:
    global _DEFAULT_PROVIDERS_REGISTERED
    if _DEFAULT_PROVIDERS_REGISTERED:
        return

    from src.libs.embedding.azure_embedding import AzureOpenAIEmbedding
    from src.libs.embedding.hash_embedding import HashEmbedding
    from src.libs.embedding.minimax_embedding import MiniMaxEmbedding
    from src.libs.embedding.ollama_embedding import OllamaEmbedding
    from src.libs.embedding.openai_embedding import OpenAIEmbedding

    EmbeddingFactory.register("azure", lambda config: AzureOpenAIEmbedding(config))
    EmbeddingFactory.register("hash", lambda config: HashEmbedding(config))
    EmbeddingFactory.register("minimax", lambda config: MiniMaxEmbedding(config))
    EmbeddingFactory.register("ollama", lambda config: OllamaEmbedding(config))
    EmbeddingFactory.register("openai", lambda config: OpenAIEmbedding(config))
    _DEFAULT_PROVIDERS_REGISTERED = True


__all__ = ["EmbeddingFactory", "EmbeddingProviderFactory", "create_embedding"]
