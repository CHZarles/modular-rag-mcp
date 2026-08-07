from __future__ import annotations

from pathlib import Path

import pytest

from src.core.index_fingerprint import IndexFingerprintGuard


def _settings(root: Path, *, model: str = "embo-01") -> dict[str, object]:
    return {
        "embedding": {"provider": "minimax", "model": model},
        "splitter": {
            "provider": "recursive",
            "chunk_size": 1000,
            "chunk_overlap": 200,
        },
        "vector_store": {"persist_path": str(root)},
    }


def test_index_fingerprint_rejects_changed_model_or_dimension(tmp_path: Path) -> None:
    IndexFingerprintGuard(_settings(tmp_path)).ensure_dimension(1536)

    IndexFingerprintGuard(_settings(tmp_path)).validate_dimension(1536)
    with pytest.raises(ValueError, match="configuration changed"):
        IndexFingerprintGuard(_settings(tmp_path, model="other")).verify_config()
    with pytest.raises(ValueError, match="dimension"):
        IndexFingerprintGuard(_settings(tmp_path)).validate_dimension(1024)


def test_existing_index_without_manifest_requires_reingestion(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest is missing"):
        IndexFingerprintGuard(_settings(tmp_path), existing_records=1).verify_config()
