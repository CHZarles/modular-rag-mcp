from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from core.settings import Settings
from libs.llm import (
    BaseVisionLLM,
    ChatResponse,
    ImageInput,
    ImagePreprocessor,
    LLMFactory,
    Message,
    create_vision_llm,
    preprocess_image,
)


class FakeVisionLLM:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.model = str(config["model"])
        self.images: list[ImageInput] = []

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: object | None = None,
        preprocessor: ImagePreprocessor | None = None,
        **kwargs: object,
    ) -> ChatResponse:
        image = preprocess_image(image, preprocessor)
        self.images.append(image)
        return ChatResponse(content=text, model=self.model)


@pytest.fixture(autouse=True)
def cleanup_fake_provider() -> Iterator[None]:
    LLMFactory.unregister_vision_provider("fake-vision")
    yield
    LLMFactory.unregister_vision_provider("fake-vision")


def _settings(vision_llm: dict[str, Any] | None = None) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai", "model": "fallback", "api_key": "test"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
        vision_llm=vision_llm,
    )


def test_vision_factory_routes_dedicated_config_to_registered_provider() -> None:
    LLMFactory.register_vision_provider("fake-vision", FakeVisionLLM)

    llm = LLMFactory.create_vision_llm(
        _settings({"provider": "fake-vision", "model": "fake-multimodal"})
    )

    assert isinstance(llm, BaseVisionLLM)
    assert llm.chat_with_image("describe", ImageInput(base64="aW1hZ2U=")).model == (
        "fake-multimodal"
    )


def test_functional_factory_accepts_direct_vision_config_mapping() -> None:
    LLMFactory.register_vision("fake-vision", FakeVisionLLM)

    llm = create_vision_llm({"provider": "fake-vision", "model": "inline"})

    assert llm.chat_with_image("describe", ImageInput(data=b"image")).model == "inline"


def test_vision_factory_falls_back_to_text_llm_config() -> None:
    llm = create_vision_llm(_settings())

    assert isinstance(llm, BaseVisionLLM)
    assert llm.model == "fallback"


@pytest.mark.parametrize(
    "image",
    [
        ImageInput(path=Path("diagram.png")),
        ImageInput(data=b"image-bytes", mime_type="image/jpeg"),
        ImageInput(base64="aW1hZ2U=", mime_type="image/webp"),
    ],
)
def test_vision_interface_accepts_path_bytes_and_base64(image: ImageInput) -> None:
    llm = FakeVisionLLM({"model": "fake"})

    llm.chat_with_image("describe", image)

    assert llm.images == [image]


def test_vision_interface_exposes_image_preprocessor_hook() -> None:
    llm = FakeVisionLLM({"model": "fake"})

    llm.chat_with_image(
        "describe",
        ImageInput(path="large.png"),
        preprocessor=lambda _: ImageInput(data=b"compressed", mime_type="image/jpeg"),
    )

    assert llm.images == [ImageInput(data=b"compressed", mime_type="image/jpeg")]


def test_preprocessor_must_return_image_input() -> None:
    with pytest.raises(TypeError, match="must return ImageInput"):
        preprocess_image(ImageInput(data=b"image"), lambda _: b"invalid")  # type: ignore[arg-type,return-value]


def test_vision_factory_names_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported Vision LLM provider: missing"):
        create_vision_llm({"vision_llm": {"provider": "missing"}})


def test_vision_factory_requires_provider() -> None:
    with pytest.raises(ValueError, match=r"vision_llm\.provider"):
        create_vision_llm({"vision_llm": {}})
