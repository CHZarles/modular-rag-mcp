"""Vision LLM factory — routes ``settings.vision_llm.provider`` to a
``BaseVisionLLM``."""

from __future__ import annotations

from typing import Callable

from src.core.settings import VisionLLMConfig
from src.ports.llm import BaseVisionLLM

VisionProviderBuilder = Callable[[VisionLLMConfig], BaseVisionLLM]


class VisionLLMFactoryError(ValueError):
    """Raised when a vision LLM provider is requested but not registered."""


class VisionLLMFactory:
    """Registry-based factory for ``BaseVisionLLM`` providers."""

    _registry: dict[str, VisionProviderBuilder] = {}

    @classmethod
    def register(cls, provider: str, builder: VisionProviderBuilder) -> None:
        if not provider or not isinstance(provider, str):
            raise VisionLLMFactoryError(
                f"provider must be a non-empty string, got {provider!r}"
            )
        if not callable(builder):
            raise VisionLLMFactoryError(f"builder for {provider!r} must be callable")
        cls._registry[provider] = builder

    @classmethod
    def unregister(cls, provider: str) -> None:
        cls._registry.pop(provider, None)

    @classmethod
    def registered_providers(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, config: VisionLLMConfig) -> BaseVisionLLM:
        builder = cls._registry.get(config.provider)
        if builder is None:
            raise VisionLLMFactoryError(
                f"unknown vision LLM provider {config.provider!r}; "
                f"registered={cls.registered_providers() or 'none'}"
            )
        return builder(config)

    @classmethod
    def reset(cls) -> None:
        cls._registry.clear()