from __future__ import annotations

import base64
import json
from email.message import Message as HTTPHeaders
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from PIL import Image

from libs.llm import AzureVisionLLM, BaseVisionLLM, ImageInput, LLMFactory, Message


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _config(**overrides: object) -> dict[str, object]:
    return {
        "provider": "azure",
        "azure_endpoint": "https://example.openai.azure.com",
        "api_version": "2024-10-21",
        "deployment_name": "gpt-4o-vision",
        "api_key": "secret",
        **overrides,
    }


def _response() -> dict[str, object]:
    return {
        "model": "gpt-4o-vision",
        "choices": [{"message": {"content": "An architecture diagram."}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
    }


def _image_bytes(size: tuple[int, int], image_format: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(20, 100, 180)).save(buffer, format=image_format)
    return buffer.getvalue()


def _request_payload(request: Request) -> dict[str, object]:
    return cast(dict[str, object], json.loads(cast(bytes, request.data).decode("utf-8")))


def _image_url(payload: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], payload["messages"])
    content = cast(list[dict[str, object]], messages[-1]["content"])
    image_url = cast(dict[str, str], content[-1]["image_url"])
    return image_url["url"]


def test_factory_creates_azure_vision_client_from_dedicated_config() -> None:
    llm = LLMFactory.create_vision_llm({"vision_llm": _config()})

    assert isinstance(llm, AzureVisionLLM)
    assert isinstance(llm, BaseVisionLLM)
    assert llm.model == "gpt-4o-vision"
    assert llm.max_image_size == 2048


def test_chat_with_path_posts_azure_multimodal_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Request, float]] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append((request, timeout))
        return FakeHTTPResponse(_response())

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(_image_bytes((40, 20)))
    llm = AzureVisionLLM(_config(timeout_seconds=7))

    result = llm.chat_with_image(
        "Describe the diagram.",
        ImageInput(path=image_path),
        messages=[Message(role="system", content="Be precise.")],
    )

    request, timeout = captured[0]
    assert result.content == "An architecture diagram."
    assert request.full_url == (
        "https://example.openai.azure.com/openai/deployments/gpt-4o-vision/"
        "chat/completions?api-version=2024-10-21"
    )
    assert dict(request.header_items())["Api-key"] == "secret"
    assert timeout == 7
    payload = _request_payload(request)
    assert "model" not in payload
    assert cast(list[dict[str, object]], payload["messages"])[0] == {
        "role": "system",
        "content": "Be precise.",
    }
    assert _image_url(payload).startswith("data:image/png;base64,")


def test_chat_accepts_raw_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(_response())

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    encoded = base64.b64encode(_image_bytes((16, 16))).decode("ascii")

    AzureVisionLLM(_config()).chat_with_image(
        "Describe.",
        ImageInput(base64=encoded, mime_type="image/png"),
    )

    assert _image_url(_request_payload(captured[0])) == f"data:image/png;base64,{encoded}"


def test_oversized_image_is_compressed_to_configured_max_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(_response())

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = AzureVisionLLM(_config(max_image_size=100))

    llm.chat_with_image("Describe.", ImageInput(data=_image_bytes((400, 200))))

    encoded = _image_url(_request_payload(captured[0])).split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as compressed:
        assert compressed.size == (100, 50)


def test_timeout_has_clear_azure_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        raise TimeoutError("request timed out")

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = AzureVisionLLM(_config())

    with pytest.raises(RuntimeError, match="azure TimeoutError: request timed out"):
        llm.chat_with_image("Describe.", ImageInput(data=_image_bytes((10, 10))))


def test_auth_failure_preserves_http_and_azure_error_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        body = BytesIO(b'{"error":{"code":"InvalidAuthenticationToken"}}')
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=HTTPHeaders(),
            fp=body,
        )

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = AzureVisionLLM(_config())

    with pytest.raises(RuntimeError) as exc_info:
        llm.chat_with_image("Describe.", ImageInput(data=_image_bytes((10, 10))))

    assert "azure HTTPError: 401" in str(exc_info.value)
    assert "InvalidAuthenticationToken" in str(exc_info.value)


@pytest.mark.parametrize("value", [0, -1, 10.5, True])
def test_max_image_size_must_be_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="max_image_size must be a positive integer"):
        AzureVisionLLM(_config(max_image_size=value))


def test_azure_endpoint_is_required() -> None:
    config = _config()
    config.pop("azure_endpoint")

    with pytest.raises(ValueError, match="missing azure_endpoint"):
        AzureVisionLLM(config)
