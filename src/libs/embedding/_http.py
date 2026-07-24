"""HTTP helper for embedding providers (OpenAI / Azure / Ollama)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

Transport = Callable[[str, bytes, dict[str, str]], dict[str, Any]]


class EmbeddingHTTPError(RuntimeError):
    """Raised when an embedding HTTP call fails."""


def _default_transport(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise EmbeddingHTTPError(f"network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise EmbeddingHTTPError(f"invalid JSON: {exc}") from exc


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    transport: Transport | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    return (transport or _default_transport)(url, body, headers)