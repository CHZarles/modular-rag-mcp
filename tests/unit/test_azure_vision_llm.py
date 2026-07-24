"""Tests for B9: Azure Vision LLM (mock HTTP)."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from src.core.settings import VisionLLMConfig
from src.libs.llm._http import LLMHTTPError
from src.libs.llm.azure_vision_llm import AzureVisionLLM
from src.libs.llm.vision_llm_factory import VisionLLMFactory
from src.ports.llm import ImageInput


def _ok_response(content: str = "an image of a cat") -> dict[str, Any]:
    return {
        "id": "vision-1",
        "model": "gpt-4o",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    }


def test_azure_vision_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="azure_endpoint"):
        AzureVisionLLM(VisionLLMConfig(provider="azure"))


def test_azure_vision_uses_base64_input() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        captured["body"] = json.loads(body)
        return _ok_response()

    b64 = base64.b64encode(b"\x89PNG_FAKE_BYTES").decode("ascii")
    vlm = AzureVisionLLM(
        VisionLLMConfig(
            provider="azure", model="gpt-4o", azure_endpoint="https://x.com", api_key="k"
        ),
        transport=transport,
    )
    response = vlm.chat_with_image("describe", ImageInput(base64=b64, mime_type="image/png"))
    assert response.content == "an image of a cat"
    assert captured["url"].endswith("/chat/completions?api-version=2024-02-01")
    # body must include image_url with a data: URL
    messages = captured["body"]["messages"]
    assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_azure_vision_uses_image_path(tmp_path) -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["body"] = json.loads(body)
        return _ok_response("from path")

    png_path = tmp_path / "img.png"
    png_path.write_bytes(b"\x89PNG_FAKE")

    vlm = AzureVisionLLM(
        VisionLLMConfig(
            provider="azure", azure_endpoint="https://x.com"
        ),
        transport=transport,
    )
    response = vlm.chat_with_image(
        "describe", ImageInput(path=str(png_path), mime_type="image/png")
    )
    assert response.content == "from path"
    data_url = captured["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")


def test_azure_vision_uses_api_key_header() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["headers"] = headers
        return _ok_response()

    vlm = AzureVisionLLM(
        VisionLLMConfig(
            provider="azure", azure_endpoint="https://x.com", api_key="vk"
        ),
        transport=transport,
    )
    vlm.chat_with_image("describe", ImageInput(base64="AAAA", mime_type="image/png"))
    assert captured["headers"]["api-key"] == "vk"


def test_azure_vision_blank_text_raises() -> None:
    vlm = AzureVisionLLM(
        VisionLLMConfig(provider="azure", azure_endpoint="https://x.com"),
        transport=lambda u, b, h: _ok_response(),
    )
    with pytest.raises(ValueError, match="non-empty string"):
        vlm.chat_with_image("", ImageInput(base64="x"))


def test_azure_vision_malformed_response_raises() -> None:
    vlm = AzureVisionLLM(
        VisionLLMConfig(provider="azure", azure_endpoint="https://x.com"),
        transport=lambda u, b, h: {"choices": []},
    )
    with pytest.raises(LLMHTTPError, match="malformed response"):
        vlm.chat_with_image("describe", ImageInput(base64="x"))


def test_factory_routes_azure_vision() -> None:
    snapshot = dict(VisionLLMFactory._registry)
    VisionLLMFactory.reset()
    try:
        VisionLLMFactory.register(
            "azure",
            lambda c: AzureVisionLLM(
                c, transport=lambda u, b, h: _ok_response()
            ),
        )
        vlm = VisionLLMFactory.create(
            VisionLLMConfig(provider="azure", azure_endpoint="https://x.com")
        )
        assert isinstance(vlm, AzureVisionLLM)
    finally:
        VisionLLMFactory._registry.clear()
        VisionLLMFactory._registry.update(snapshot)