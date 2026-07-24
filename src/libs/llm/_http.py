"""HTTP helper for OpenAI-compatible LLM providers.

Kept tiny so tests can swap in a fake transport by passing a custom
``transport`` callable to :func:`post_json`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

# A transport takes (url, body, headers) and returns parsed JSON.
# Returning a dict means "use this as the parsed response"; raising an
# exception means "treat as a transport failure" — the exception's
# message is surfaced to callers with the provider name prepended.
Transport = Callable[[str, bytes, dict[str, str]], dict[str, Any]]


class LLMHTTPError(RuntimeError):
    """Raised when an LLM HTTP call fails. Provider name is prepended in ctor."""


def _default_transport(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """Real-network transport using ``urllib``. Only invoked when no fake
    transport has been registered (i.e., in production use, not in tests)."""
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:  # covers network errors + HTTPError
        raise LLMHTTPError(f"network error: {exc.reason}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMHTTPError(f"invalid JSON response: {exc}") from exc


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    transport: Transport | None = None,
) -> dict[str, Any]:
    """POST ``payload`` as JSON to ``url``. Returns the parsed response body."""
    body = json.dumps(payload).encode("utf-8")
    fn = transport or _default_transport
    return fn(url, body, headers)