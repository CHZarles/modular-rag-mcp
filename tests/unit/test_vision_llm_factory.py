"""Tests for B8: vision LLM factory routing."""

from __future__ import annotations

import pytest

from src.core.settings import VisionLLMConfig
from src.libs.llm.vision_llm_factory import (
    VisionLLMFactory,
    VisionLLMFactoryError,
)
from src.ports.llm import BaseVisionLLM, ChatResponse, ImageInput, Message


class FakeVisionLLM(BaseVisionLLM):
    """Test double for vision-capable LLMs."""

    instances: list["FakeVisionLLM"] = []

    def __init__(self, config: VisionLLMConfig) -> None:
        self.config = config
        self.calls: list[tuple[str, ImageInput]] = []
        FakeVisionLLM.instances.append(self)

    def chat_with_image(  # type: ignore[override]
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace=None,
        **kwargs,
    ) -> ChatResponse:
        self.calls.append((text, image))
        return ChatResponse(content=f"vision:{text}", model="fake-vision")


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(VisionLLMFactory._registry)
    VisionLLMFactory.reset()
    yield
    VisionLLMFactory._registry.clear()
    VisionLLMFactory._registry.update(snapshot)


def test_factory_returns_base_vision_llm_instance() -> None:
    VisionLLMFactory.register("fake", lambda c: FakeVisionLLM(c))
    vlm = VisionLLMFactory.create(VisionLLMConfig(provider="fake"))
    assert isinstance(vlm, BaseVisionLLM)
    assert isinstance(vlm, FakeVisionLLM)


def test_factory_accepts_canonical_provider_names() -> None:
    for name in ("azure", "dashscope"):
        VisionLLMFactory.register(name, lambda c: FakeVisionLLM(c))
    assert set(VisionLLMFactory.registered_providers()) == {"azure", "dashscope"}


def test_factory_unknown_provider_raises_with_hint() -> None:
    with pytest.raises(VisionLLMFactoryError) as ei:
        VisionLLMFactory.create(VisionLLMConfig(provider="unknown"))
    assert "unknown" in str(ei.value)
    assert "registered=" in str(ei.value)


def test_factory_rejects_empty_provider_name() -> None:
    with pytest.raises(VisionLLMFactoryError, match="non-empty"):
        VisionLLMFactory.register("", lambda c: FakeVisionLLM(c))


def test_factory_rejects_non_callable_builder() -> None:
    with pytest.raises(VisionLLMFactoryError, match="callable"):
        VisionLLMFactory.register("oops", 42)  # type: ignore[arg-type]


def test_factory_reset_clears_registry() -> None:
    VisionLLMFactory.register("fake", lambda c: FakeVisionLLM(c))
    VisionLLMFactory.reset()
    assert VisionLLMFactory.registered_providers() == []


def test_vision_call_round_trip() -> None:
    VisionLLMFactory.register("fake", lambda c: FakeVisionLLM(c))
    vlm = VisionLLMFactory.create(VisionLLMConfig(provider="fake"))
    img = ImageInput(path="/tmp/test.png", mime_type="image/png")
    resp = vlm.chat_with_image("describe", img)
    assert resp.content == "vision:describe"
    assert vlm.calls[0][0] == "describe"
    assert vlm.calls[0][1].path == "/tmp/test.png"