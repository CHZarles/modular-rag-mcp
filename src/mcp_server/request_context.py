"""Per-request identity carried through MCP HTTP into the Tool execution.

The upper-layer business system sends three anonymous identifiers
(``X-Request-ID``, ``X-RAG-Actor-Key``, ``X-RAG-Session-ID``) so the component
can attribute Traces without ever seeing a real user identity. The Stdio
transport has no Header surface, so it must always generate a fresh
``request_id`` and leave ``actor_key`` / ``session_id`` unset.

The module owns:

* ``RequestContext`` frozen dataclass carrying the three identifiers
* ``current_request_context`` ContextVar — propagated through
  ``asyncio.to_thread()`` because each Tool call is dispatched to a worker
  thread inside the MCP SDK
* ``request_context_from_headers()`` — strict Header parser
* ``get_request_context()`` — current value or a freshly generated fallback
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

MAX_IDENTIFIER_CHARS: Final = 128
PRINTABLE_HEADERS: Final = frozenset(
    string.ascii_letters + string.digits + string.punctuation + " "
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_REQUEST_ID_VAR: ContextVar[RequestContext | None] = ContextVar(
    "mcp_request_context", default=None
)


@dataclass(frozen=True)
class RequestContext:
    """Stable identity shared by all work happening inside one MCP call."""

    request_id: str
    actor_key: str | None = None
    session_id: str | None = None


class InvalidRequestHeaderError(ValueError):
    """Header value failed plan §5.5 validation."""

    def __init__(self, header: str, reason: str) -> None:
        super().__init__(f"{header}: {reason}")
        self.header = header
        self.reason = reason


def current_request_context() -> RequestContext | None:
    """Return the active ``RequestContext`` for the current logical call."""
    return _REQUEST_ID_VAR.get()


def bind_request_context(context: RequestContext):
    """Set the active context; return a ``Reset`` token used to restore it.

    ``ProtocolHandler`` calls ``reset(token)`` in a ``finally`` block so a
    Tool failure cannot leak the context into the next request handled by
    the same worker thread.
    """
    return _REQUEST_ID_VAR.set(context)


def reset_request_context(token) -> None:
    """Restore the previous ContextVar value after a Tool call returns."""
    _REQUEST_ID_VAR.reset(token)


def get_request_context() -> RequestContext:
    """Return the active context, or generate a fresh stdio fallback.

    Stdio MCP has no Header surface (plan §5.5 / FR-13); this helper returns
    a synthetic context so downstream code never has to special-case None.
    """
    active = _REQUEST_ID_VAR.get()
    if active is not None:
        return active
    return RequestContext(request_id=_new_request_id())


def generate_request_id() -> str:
    """Public generator so tests and the MCP handler share one ID strategy."""
    return _new_request_id()


def request_context_from_headers(
    headers: Mapping[str, str] | None,
) -> RequestContext:
    """Build a ``RequestContext`` from a Header mapping.

    Mirrors the validation in plan §5.5: any control character, blank value,
    or length violation raises ``InvalidRequestHeaderError``, which the
    protocol handler maps to MCP ``INVALID_PARAMS``.
    """
    mapping = {key.lower(): value for key, value in (headers or {}).items()}

    def _read(header: str) -> str | None:
        try:
            value = mapping[header.lower()]
        except KeyError:
            return None
        if not isinstance(value, str):
            raise InvalidRequestHeaderError(header, "header value must be a string")
        return _clean_header_value(header, value)

    request_id = _read("X-Request-ID") or _new_request_id()
    actor_key = _read("X-RAG-Actor-Key")
    session_id = _read("X-RAG-Session-ID")
    return RequestContext(
        request_id=request_id,
        actor_key=actor_key,
        session_id=session_id,
    )


def _clean_header_value(header: str, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvalidRequestHeaderError(header, "header value must not be blank")
    if len(stripped) > MAX_IDENTIFIER_CHARS:
        raise InvalidRequestHeaderError(
            header, f"header value must not exceed {MAX_IDENTIFIER_CHARS} characters"
        )
    if _CONTROL_CHARS_RE.search(stripped):
        raise InvalidRequestHeaderError(header, "header value contains control characters")
    for character in stripped:
        if character not in PRINTABLE_HEADERS:
            raise InvalidRequestHeaderError(
                header, f"header value contains unsupported character {character!r}"
            )
    return stripped


def _new_request_id() -> str:
    """Generate a stable opaque ID for the Stdio fallback and accepted trace IDs."""
    return secrets.token_hex(16)


__all__ = [
    "InvalidRequestHeaderError",
    "MAX_IDENTIFIER_CHARS",
    "RequestContext",
    "bind_request_context",
    "current_request_context",
    "generate_request_id",
    "get_request_context",
    "request_context_from_headers",
    "reset_request_context",
]
