"""LLM factory: route ``settings.llm.provider`` to a concrete ``BaseLLM``.

Stage B1 scope:

- Provide a registry-based factory so the rest of the codebase (C1, D8, E1)
  can depend on ``BaseLLM`` without knowing the concrete provider.
- Concrete adapters (``B7.1 OpenAI-compatible``, ``B7.2 Ollama``, ...) are
  registered later via ``LLMFactory.register``. This module ships empty by
  design: it owns the routing surface, not the implementations.
"""

from __future__ import annotations

from typing import Callable

from src.core.settings import LLMConfig
from src.ports.llm import BaseLLM

# A provider builder takes an ``LLMConfig`` and returns a ``BaseLLM``.
ProviderBuilder = Callable[[LLMConfig], BaseLLM]


class LLMFactoryError(ValueError):
    """Raised when an LLM provider is requested but not registered."""


class LLMFactory:
    """Registry-based factory for ``BaseLLM`` providers.

    Usage::

        LLMFactory.register("openai", lambda cfg: OpenAILLM(cfg))
        llm = LLMFactory.create(settings.llm)
    """

    _registry: dict[str, ProviderBuilder] = {}

    @classmethod
    def register(cls, provider: str, builder: ProviderBuilder) -> None:
        """Register ``builder`` under the name ``provider``.

        Re-registration overwrites the previous builder — useful for tests
        that need to swap a real provider with a Fake.
        """
        if not provider or not isinstance(provider, str):
            raise LLMFactoryError(f"provider name must be a non-empty string, got {provider!r}")
        if not callable(builder):
            raise LLMFactoryError(f"builder for {provider!r} must be callable")
        cls._registry[provider] = builder

    @classmethod
    def unregister(cls, provider: str) -> None:
        """Remove a registered provider. Missing keys are ignored."""
        cls._registry.pop(provider, None)

    @classmethod
    def registered_providers(cls) -> list[str]:
        """Return the list of currently registered provider names."""
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, config: LLMConfig) -> BaseLLM:
        """Instantiate a ``BaseLLM`` for the provider named in ``config``.

        Raises:
            LLMFactoryError: If ``config.provider`` is not registered.
        """
        provider = config.provider
        builder = cls._registry.get(provider)
        if builder is None:
            raise LLMFactoryError(
                f"unknown LLM provider {provider!r}; "
                f"registered={cls.registered_providers() or 'none'}"
            )
        return builder(config)

    @classmethod
    def reset(cls) -> None:
        """Wipe the registry. Intended for tests only."""
        cls._registry.clear()