"""MCP Tool adapter base, runtime validators, and input-boundary constants.

The constants here are the single source of truth for the input boundary
that the JSON Schema and the runtime both enforce. Both layers reference
the same ``MAX_*`` constants so the wire contract and the Python checks can
never drift.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

from src.core.types import JsonDict
from src.core.wire_safety import (
    PUBLIC_CITATION_METADATA_KEYS,
    public_citation_metadata,
    public_source_label,
    sanitize_wire_string,
)
from src.libs.loader.format_router import DOCUMENT_TYPES

MAX_QUERY_CHARS: Final = 4000
MAX_TOP_K: Final = 20
MAX_IDENTIFIER_CHARS: Final = 128

class ToolArgumentError(ValueError):
    """Raised when Tool input fails Schema or runtime boundary validation."""


class ToolExecutionError(RuntimeError):
    """Raised when a Tool execution itself fails.

    Carries a stable component code (see plan §5.4) so the wire envelope can
    expose only the public code without leaking the original exception.
    """

    def __init__(self, component_code: str) -> None:
        super().__init__(component_code)
        self.component_code = component_code


@runtime_checkable
class ToolHandler(Protocol):
    """MCP Tool contract for name, Schema, and invocation."""

    name: str
    description: str
    input_schema: JsonDict

    def call(self, arguments: JsonDict) -> JsonDict: ...


def validate_query(value: Any) -> str:
    """Trimmed query content must be a non-empty printable-ish string.

    Whitespace-only input is rejected by ``_normalize_non_empty_string``, which
    trims first and then enforces non-emptiness at the field length cap.
    """
    return _normalize_non_empty_string(
        value, field="query", max_length=MAX_QUERY_CHARS
    )


def validate_top_k(value: Any, *, default: int = 5) -> int:
    if value is None:
        value = default
    # ``bool`` is a subclass of ``int``; reject explicitly so True/False can't
    # sneak through as a numeric value.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError("top_k must be a positive integer")
    if value < 1 or value > MAX_TOP_K:
        raise ToolArgumentError(
            f"top_k must be between 1 and {MAX_TOP_K}"
        )
    return value


def validate_identifier(value: Any, *, field: str) -> str:
    """Validate collection, doc_id, and other stable MCP-facing identifiers."""
    return _normalize_non_empty_string(
        value, field=field, max_length=MAX_IDENTIFIER_CHARS
    )


def validate_file_type(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in DOCUMENT_TYPES:
        raise ToolArgumentError(
            f"file_type must be one of: {', '.join(DOCUMENT_TYPES)}"
        )
    return value


def _normalize_non_empty_string(
    value: Any, *, field: str, max_length: int
) -> str:
    if not isinstance(value, str):
        raise ToolArgumentError(f"{field} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ToolArgumentError(f"{field} must be a non-empty string")
    if len(stripped) > max_length:
        raise ToolArgumentError(
            f"{field} must not exceed {max_length} characters"
        )
    return stripped


__all__ = [
    "DOCUMENT_TYPES",
    "MAX_IDENTIFIER_CHARS",
    "MAX_QUERY_CHARS",
    "MAX_TOP_K",
    "PUBLIC_CITATION_METADATA_KEYS",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolHandler",
    "public_citation_metadata",
    "public_source_label",
    "sanitize_wire_string",
    "validate_identifier",
    "validate_file_type",
    "validate_query",
    "validate_top_k",
]
