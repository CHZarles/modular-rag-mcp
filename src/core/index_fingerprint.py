"""Keep vector indexes tied to the configuration that created them."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TOKENIZER_VERSION = "sparse-cjk-bigram-title-v2"
_MANIFEST_VERSION = 1


class IndexFingerprintGuard:
    """Reject silent mixing of incompatible embedding or chunking settings."""

    def __init__(
        self,
        settings: Any,
        *,
        existing_records: int = 0,
    ) -> None:
        vector_store = _section(settings, "vector_store")
        persist_path = str(vector_store.get("persist_path", "./data/db/chroma")).strip()
        if not persist_path:
            raise ValueError("index fingerprint requires vector_store.persist_path")
        self.path = Path(persist_path).expanduser() / "index_manifest.json"
        self.config = _config_payload(settings)
        self.existing_records = existing_records

    def verify_config(self) -> None:
        manifest = self._read()
        if manifest is None:
            if self.existing_records:
                raise ValueError(
                    "index manifest is missing for an existing index; re-ingest all documents"
                )
            return
        if manifest.get("config") != self.config:
            raise ValueError("index configuration changed; re-ingest all documents")

    def ensure_dimension(self, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("index fingerprint dimension must be positive")
        self.verify_config()
        manifest = self._read()
        if manifest is not None:
            if manifest.get("dimension") != dimension:
                raise ValueError("embedding dimension changed; re-ingest all documents")
            return
        payload = {
            "version": _MANIFEST_VERSION,
            "config": self.config,
            "dimension": dimension,
        }
        payload["fingerprint"] = _fingerprint(payload)
        self._write(payload)

    def validate_dimension(self, dimension: int) -> None:
        self.verify_config()
        manifest = self._read()
        if manifest is not None and manifest.get("dimension") != dimension:
            raise ValueError("query embedding dimension does not match the index")

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"index manifest is invalid: {self.path.name}") from exc
        if not isinstance(payload, dict) or payload.get("version") != _MANIFEST_VERSION:
            raise ValueError("index manifest version is unsupported; re-ingest all documents")
        if payload.get("fingerprint") != _fingerprint(
            {key: payload.get(key) for key in ("version", "config", "dimension")}
        ):
            raise ValueError("index manifest fingerprint is invalid; re-ingest all documents")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix="index_manifest.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _config_payload(settings: Any) -> dict[str, Any]:
    embedding = _section(settings, "embedding")
    splitter = _section(settings, "splitter")
    return {
        "embedding": {
            key: embedding.get(key)
            for key in ("provider", "model", "dimension")
            if embedding.get(key) is not None
        },
        "splitter": {
            key: splitter.get(key)
            for key in ("provider", "chunk_size", "chunk_overlap")
            if splitter.get(key) is not None
        },
        "tokenizer_version": TOKENIZER_VERSION,
    }


def _section(settings: Any, name: str) -> Mapping[str, Any]:
    value = settings.get(name) if isinstance(settings, Mapping) else getattr(settings, name, None)
    if not isinstance(value, Mapping):
        raise ValueError(f"index fingerprint requires settings.{name}")
    return value


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["IndexFingerprintGuard", "TOKENIZER_VERSION"]
