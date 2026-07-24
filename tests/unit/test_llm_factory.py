"""Tests for B1: LLM factory routing."""

from __future__ import annotations

import pytest

from src.core.settings import LLMConfig
from src.libs.llm.llm_factory import LLMFactory, LLMFactoryError
from src.ports.llm import BaseLLM, ChatResponse, Message


class FakeLLM(BaseLLM):
    """Minimal in-test LLM that returns a fixed response and records calls."""

    instances: list["FakeLLM"] = []

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.calls: list[list[Message]] = []
        FakeLLM.instances.append(self)

    def chat(self, messages: list[Message], trace=None, **kwargs):  # type: ignore[override]
        self.calls.append(list(messages))
        return ChatResponse(
            content=f"fake:{messages[-1].content}",
            model=self.config.model or "fake-model",
        )


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot + restore the factory registry around each test."""
    snapshot = dict(LLMFactory._registry)
    LLMFactory.reset()
    yield
    LLMFactory._registry.clear()
    LLMFactory._registry.update(snapshot)


def test_register_and_create_routes_by_provider() -> None:
    """A registered provider should be constructed with the supplied config."""
    LLMFactory.register("fake", lambda cfg: FakeLLM(cfg))
    config = LLMConfig(provider="fake", model="test-model")

    llm = LLMFactory.create(config)

    assert isinstance(llm, FakeLLM)
    assert llm.config.model == "test-model"


def test_factory_returns_base_llm_instance() -> None:
    """LLMFactory.create must return a BaseLLM-compatible object."""
    LLMFactory.register("fake", lambda cfg: FakeLLM(cfg))
    llm = LLMFactory.create(LLMConfig(provider="fake"))
    assert isinstance(llm, BaseLLM)


def test_factory_chat_round_trip() -> None:
    """Round-trip: send messages, get a response that references them."""
    LLMFactory.register("fake", lambda cfg: FakeLLM(cfg))
    llm = LLMFactory.create(LLMConfig(provider="fake"))

    response = llm.chat([Message(role="user", content="hello")])

    assert response.content == "fake:hello"
    assert response.model == "fake-model"


def test_create_unknown_provider_raises() -> None:
    """Unregistered provider names surface as LLMFactoryError with a hint."""
    with pytest.raises(LLMFactoryError) as ei:
        LLMFactory.create(LLMConfig(provider="nonexistent"))
    msg = str(ei.value)
    assert "nonexistent" in msg
    assert "registered=" in msg


def test_register_rejects_empty_provider_name() -> None:
    LLMFactory.reset()
    with pytest.raises(LLMFactoryError, match="non-empty"):
        LLMFactory.register("", lambda c: FakeLLM(c))


def test_register_rejects_non_callable_builder() -> None:
    LLMFactory.reset()
    with pytest.raises(LLMFactoryError, match="callable"):
        LLMFactory.register("oops", "not a callable")  # type: ignore[arg-type]


def test_register_overwrites_existing_provider() -> None:
    """Re-registration replaces the previous builder (useful for test swaps)."""
    LLMFactory.register("fake", lambda c: FakeLLM(c))
    LLMFactory.register("fake", lambda c: FakeLLM(c))  # second time
    assert "fake" in LLMFactory.registered_providers()


def test_factory_accepts_providers_typical_names() -> None:
    """Spot-check the canonical provider names from DEV_SPEC §5.5 register cleanly."""
    LLMFactory.reset()
    for name in ("azure", "openai", "ollama", "deepseek"):
        LLMFactory.register(name, lambda c: FakeLLM(c))
    assert set(LLMFactory.registered_providers()) == {"azure", "openai", "ollama", "deepseek"}


def test_reset_clears_registry() -> None:
    LLMFactory.register("fake", lambda c: FakeLLM(c))
    LLMFactory.reset()
    assert LLMFactory.registered_providers() == []
    with pytest.raises(LLMFactoryError):
        LLMFactory.create(LLMConfig(provider="fake"))