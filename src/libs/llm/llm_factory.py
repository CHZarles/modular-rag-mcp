"""按配置创建 LLM 客户端。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.ports.llm import BaseLLM, BaseVisionLLM

LLMProviderFactory = Callable[[Mapping[str, Any]], BaseLLM]
VisionLLMProviderFactory = Callable[[Mapping[str, Any]], BaseVisionLLM]


class LLMFactory:
    """轻量 provider 注册表；内置供应商按需延迟注册。"""

    _providers: dict[str, LLMProviderFactory] = {}
    _vision_providers: dict[str, VisionLLMProviderFactory] = {}

    @classmethod
    def register(cls, provider: str, factory: LLMProviderFactory) -> None:
        """注册一个 provider 名称到构造函数的映射。"""
        key = provider.strip().lower()
        if not key:
            raise ValueError("LLM provider name must not be empty")
        cls._providers[key] = factory

    @classmethod
    def unregister(cls, provider: str) -> None:
        """测试或插件卸载时移除 provider。"""
        cls._providers.pop(provider.strip().lower(), None)

    @classmethod
    def register_vision_provider(
        cls,
        provider: str,
        factory: VisionLLMProviderFactory,
    ) -> None:
        """注册一个 Vision provider 到多模态客户端构造函数的映射。"""
        key = provider.strip().lower()
        if not key:
            raise ValueError("Vision LLM provider name must not be empty")
        cls._vision_providers[key] = factory

    @classmethod
    def unregister_vision_provider(cls, provider: str) -> None:
        """测试或插件卸载时移除 Vision provider。"""
        cls._vision_providers.pop(provider.strip().lower(), None)

    @classmethod
    def register_vision(
        cls,
        provider: str,
        factory: VisionLLMProviderFactory,
    ) -> None:
        """``register_vision_provider`` 的简写。"""
        cls.register_vision_provider(provider, factory)

    @classmethod
    def unregister_vision(cls, provider: str) -> None:
        """``unregister_vision_provider`` 的简写。"""
        cls.unregister_vision_provider(provider)

    @classmethod
    def create(cls, settings: Any) -> BaseLLM:
        """从 Settings 或 llm 配置字典创建对应的 LLM 客户端。"""
        _register_default_providers()
        config = _llm_config(settings)
        provider = str(config.get("provider", "")).strip().lower()
        if not provider:
            raise ValueError("Missing required setting: llm.provider")

        factory = cls._providers.get(provider)
        if factory is None:
            available = ", ".join(sorted(cls._providers)) or "none"
            raise ValueError(
                f"Unsupported LLM provider: {provider}. Registered providers: {available}"
            )
        return factory(config)

    @classmethod
    def create_vision_llm(cls, settings: Any) -> BaseVisionLLM:
        """优先从 vision_llm 配置创建客户端，缺省时复用 llm 配置。"""
        _register_default_providers()
        config = _vision_llm_config(settings)
        provider = str(config.get("provider", "")).strip().lower()
        if not provider:
            raise ValueError("Missing required setting: vision_llm.provider")

        factory = cls._vision_providers.get(provider)
        if factory is None:
            available = ", ".join(sorted(cls._vision_providers)) or "none"
            raise ValueError(
                f"Unsupported Vision LLM provider: {provider}. "
                f"Registered providers: {available}"
            )
        return factory(config)


def create_llm(settings: Any) -> BaseLLM:
    """函数式入口，便于上层装配代码少写一个类名。"""
    return LLMFactory.create(settings)


def create_vision_llm(settings: Any) -> BaseVisionLLM:
    """函数式 Vision LLM 创建入口。"""
    return LLMFactory.create_vision_llm(settings)


def _llm_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        config = settings.get("llm", settings)
    else:
        config = getattr(settings, "llm", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: llm.provider")
    return config


def _vision_llm_config(settings: Any) -> Mapping[str, Any]:
    if isinstance(settings, Mapping):
        if "vision_llm" in settings:
            config = settings.get("vision_llm")
        elif "llm" in settings:
            config = settings.get("llm")
        else:
            config = settings
    else:
        config = getattr(settings, "vision_llm", None)
        if config is None:
            config = getattr(settings, "llm", None)

    if not isinstance(config, Mapping):
        raise ValueError("Missing required setting: vision_llm.provider")
    return config


_DEFAULT_PROVIDERS_REGISTERED = False


def _register_default_providers() -> None:
    global _DEFAULT_PROVIDERS_REGISTERED
    if _DEFAULT_PROVIDERS_REGISTERED:
        return

    from src.libs.llm.azure_llm import AzureOpenAILLM
    from src.libs.llm.azure_vision_llm import AzureVisionLLM
    from src.libs.llm.deepseek_llm import DeepSeekLLM
    from src.libs.llm.ollama_llm import OllamaLLM
    from src.libs.llm.openai_llm import OpenAICompatibleLLM

    LLMFactory.register("azure", lambda config: AzureOpenAILLM(config))
    LLMFactory.register("deepseek", lambda config: DeepSeekLLM(config))
    LLMFactory.register("ollama", lambda config: OllamaLLM(config))
    LLMFactory.register("openai", lambda config: OpenAICompatibleLLM(config))
    LLMFactory.register_vision_provider("azure", lambda config: AzureVisionLLM(config))
    LLMFactory.register_vision_provider(
        "openai", lambda config: OpenAICompatibleLLM(config)
    )
    _DEFAULT_PROVIDERS_REGISTERED = True


__all__ = [
    "LLMFactory",
    "LLMProviderFactory",
    "VisionLLMProviderFactory",
    "create_llm",
    "create_vision_llm",
]
