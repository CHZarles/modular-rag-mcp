from __future__ import annotations

import json
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from libs.embedding import AzureOpenAIEmbedding, EmbeddingFactory, OpenAIEmbedding


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def embedding_payload() -> dict[str, Any]:
    return {
        "data": [
            {"index": 1, "embedding": [0.3, 0.4]},
            {"index": 0, "embedding": [0.1, 0.2]},
        ]
    }


def headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_openai_embedding_provider_posts_embeddings_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(embedding_payload())

    monkeypatch.setattr("src.libs.embedding.openai_embedding.urlopen", fake_urlopen)
    embedding = EmbeddingFactory.create(
        {
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "api_key": "secret",
            }
        }
    )

    vectors = embedding.embed(["alpha", "beta"])

    assert isinstance(embedding, OpenAIEmbedding)
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    request = captured[0]
    assert request.full_url == "https://api.openai.com/v1/embeddings"
    assert headers(request)["authorization"] == "Bearer secret"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "model": "text-embedding-3-small",
        "input": ["alpha", "beta"],
    }


def test_azure_embedding_provider_uses_deployment_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(embedding_payload())

    monkeypatch.setattr("src.libs.embedding.openai_embedding.urlopen", fake_urlopen)
    embedding = EmbeddingFactory.create(
        {
            "embedding": {
                "provider": "azure",
                "endpoint": "https://example.openai.azure.com",
                "deployment_name": "embedding-deployment",
                "api_version": "2024-02-15-preview",
                "api_key": "azure-secret",
            }
        }
    )

    vectors = embedding.embed(["alpha", "beta"])

    assert isinstance(embedding, AzureOpenAIEmbedding)
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    request = captured[0]
    assert request.full_url == (
        "https://example.openai.azure.com/openai/deployments/embedding-deployment/embeddings"
        "?api-version=2024-02-15-preview"
    )
    assert headers(request)["api-key"] == "azure-secret"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "input": ["alpha", "beta"]
    }


def test_openai_embedding_rejects_empty_or_too_long_input() -> None:
    embedding = OpenAIEmbedding(
        {"model": "text-embedding-3-small", "api_key": "secret", "max_input_chars": 4}
    )

    with pytest.raises(ValueError, match="texts must be a non-empty list"):
        embedding.embed([])
    with pytest.raises(ValueError, match=r"texts\[0\]"):
        embedding.embed(cast(Any, [123]))
    with pytest.raises(ValueError, match="max_input_chars"):
        embedding.embed(["abcde"])


def test_openai_embedding_wraps_transport_errors_without_sensitive_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        raise URLError("network down")

    monkeypatch.setattr("src.libs.embedding.openai_embedding.urlopen", fake_urlopen)
    embedding = OpenAIEmbedding({"model": "text-embedding-3-small", "api_key": "secret-key"})

    with pytest.raises(RuntimeError, match="openai URLError") as exc_info:
        embedding.embed(["alpha"])
    assert "secret-key" not in str(exc_info.value)
