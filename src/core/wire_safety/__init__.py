"""Wire-safety helpers shared by core responses and MCP adapters.

Anything destined for the public wire envelope (MCP JSON-RPC, FastAPI bodies,
log lines) must run through these helpers so absolute paths, env-secret
references, and arbitrary internal metadata never leak.
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final

from src.core.types import JsonDict

# Wire-safe citation metadata whitelist. Anything outside this set is dropped
# before the data crosses the network boundary, regardless of how it was
# assigned internally.
PUBLIC_CITATION_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {"collection", "section", "tags", "title"}
)

_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w/])(?:/|~/?)[^\s'\"]+\.[A-Za-z0-9]{1,8}"
)
_ENV_VAR_RE = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")


def public_source_label(source_path: str | None) -> str:
    """Return the public file label safe for wire exposure.

    Absolute paths, drive letters, and parent directories are considered
    internal topology. Only the final path segment is exposed; ``unknown``
    is returned when nothing usable remains.
    """
    if not isinstance(source_path, str):
        return "unknown"
    candidate = source_path.strip()
    if not candidate:
        return "unknown"
    basename = _basename(candidate)
    cleaned = basename.strip().strip(".")
    return cleaned or "unknown"


def public_citation_metadata(metadata: JsonDict | None) -> JsonDict:
    """Filter ``metadata`` down to the keys that are allowed on the wire.

    Drops ``source_path``, page numbers, image filesystem paths, generation
    claim tokens, and any other field that would expose internal topology.
    Lives next to ``PUBLIC_CITATION_METADATA_KEYS`` so whitelist enforcement
    is one mental hop away from the data it filters.
    """
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if key in PUBLIC_CITATION_METADATA_KEYS
    }


def sanitize_wire_string(value: str) -> str:
    """Defensive last-mile scrub for any text destined for the wire envelope.

    Replaces anything that looks like an absolute filesystem path or common
    environment-secret name with a public placeholder. Keeps casual ASCII
    (including slashes inside fragment text) intact.
    """

    def _scrub(match: re.Match[str]) -> str:
        token = match.group(0)
        env = token[2:-1]
        if env in os.environ:
            return "[REDACTED_ENV]"
        return token

    scrubbed = _ENV_VAR_RE.sub(_scrub, value)
    scrubbed = _ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", scrubbed)
    return scrubbed


def _basename(candidate: str) -> str:
    for splitter in (PureWindowsPath(candidate).name, PurePosixPath(candidate).name):
        if splitter:
            return splitter
    return candidate.rstrip("/\\").split("/")[-1].split("\\")[-1]


__all__ = [
    "PUBLIC_CITATION_METADATA_KEYS",
    "public_citation_metadata",
    "public_source_label",
    "sanitize_wire_string",
]
