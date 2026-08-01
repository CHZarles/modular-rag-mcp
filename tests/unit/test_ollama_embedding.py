from __future__ import annotations

import json
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from libs.embedding import BaseEmbedding, EmbeddingFactory, OllamaEmbedding


class FakeHTTPResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_factory_creates_ollama_and_posts_batch_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Request, float]] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append((request, timeout))
        return FakeHTTPResponse({"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]})

    monkeypatch.setattr("src.libs.embedding.ollama_embedding.urlopen", fake_urlopen)
    embedding = EmbeddingFactory.create(
        {"embedding": {"provider": "ollama", "model": "nomic-embed-text"}}
    )

    vectors = embedding.embed(["alpha", "beta"])

    assert isinstance(embedding, OllamaEmbedding)
    assert isinstance(embedding, BaseEmbedding)
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    request, timeout = captured[0]
    assert request.full_url == "http://localhost:11434/api/embed"
    assert request.method == "POST"
    assert timeout == 30
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "model": "nomic-embed-text",
        "input": ["alpha", "beta"],
    }


def test_single_input_uses_custom_base_url_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Request, float]] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append((request, timeout))
        return FakeHTTPResponse({"embeddings": [[1, 2]]})

    monkeypatch.setattr("src.libs.embedding.ollama_embedding.urlopen", fake_urlopen)
    embedding = OllamaEmbedding(
        {
            "model": "mxbai-embed-large",
            "base_url": "http://127.0.0.1:11434/",
            "timeout_seconds": 4.5,
        }
    )

    assert embedding.embed(["one"]) == [[1.0, 2.0]]
    request, timeout = captured[0]
    assert request.full_url == "http://127.0.0.1:11434/api/embed"
    assert timeout == 4.5


def test_ollama_embedding_requires_model() -> None:
    with pytest.raises(ValueError, match="ollama configuration error: missing model"):
        OllamaEmbedding({})


def test_ollama_embedding_rejects_empty_invalid_or_too_long_input() -> None:
    embedding = OllamaEmbedding({"model": "nomic-embed-text", "max_input_chars": 4})

    with pytest.raises(ValueError, match="texts must be a non-empty list"):
        embedding.embed([])
    with pytest.raises(ValueError, match=r"texts\[0\]"):
        embedding.embed(cast(Any, [123]))
    with pytest.raises(ValueError, match="max_input_chars"):
        embedding.embed(["abcde"])


@pytest.mark.parametrize(
    ("error", "error_type"),
    [(URLError("connection refused"), "URLError"), (TimeoutError("timed out"), "TimeoutError")],
)
def test_ollama_embedding_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    error_type: str,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        raise error

    monkeypatch.setattr("src.libs.embedding.ollama_embedding.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match=f"ollama {error_type}"):
        OllamaEmbedding({"model": "nomic-embed-text"}).embed(["alpha"])


@pytest.mark.parametrize(
    ("payload", "texts", "message"),
    [
        ({}, ["alpha"], "missing embeddings"),
        ({"embeddings": [[0.1], [0.2]]}, ["alpha"], "count does not match"),
        ({"embeddings": [[0.1], [0.2, 0.3]]}, ["alpha", "beta"], "same dimension"),
        ({"embeddings": [[float("nan")]]}, ["alpha"], "finite numbers"),
        ({"embeddings": [[]]}, ["alpha"], "non-empty list"),
    ],
)
def test_ollama_embedding_rejects_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    texts: list[str],
    message: str,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        return FakeHTTPResponse(payload)

    monkeypatch.setattr("src.libs.embedding.ollama_embedding.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match=message):
        OllamaEmbedding({"model": "nomic-embed-text"}).embed(texts)
