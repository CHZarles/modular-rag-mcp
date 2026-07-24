"""Tests for A3: settings load + validate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.core.settings import (
    Settings,
    SettingsError,
    load_settings,
    validate_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"


def test_load_default_settings_yaml_succeeds() -> None:
    """The shipped ``config/settings.yaml`` must parse without errors."""
    settings = load_settings(DEFAULT_SETTINGS)
    assert isinstance(settings, Settings)
    assert settings.llm.provider == "azure"
    assert settings.embedding.provider == "openai"
    assert settings.vector_store.backend == "chroma"
    assert settings.retrieval.top_k_final == 10
    assert settings.dashboard.port == 8501


def test_load_settings_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    """A non-existent path raises ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "absent.yaml")


def test_load_settings_with_invalid_yaml_raises_settingserror(tmp_path: Path) -> None:
    """Malformed YAML surfaces as ``SettingsError``."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("knowledge_service: [unclosed", encoding="utf-8")
    with pytest.raises(SettingsError, match="invalid YAML"):
        load_settings(bad)


def test_load_settings_with_missing_required_field_has_readable_path(tmp_path: Path) -> None:
    """Dropping ``embedding.provider`` must produce a SettingsError that
    names the dotted field path."""
    cfg = tmp_path / "no_embedding_provider.yaml"
    cfg.write_text(
        """
knowledge_service: {mode: local}
llm: {provider: azure}
embedding: {}
vision_llm: {provider: azure}
vector_store: {backend: chroma}
retrieval: {sparse_backend: bm25}
rerank: {backend: none}
""",
        encoding="utf-8",
    )
    with pytest.raises(SettingsError) as ei:
        load_settings(cfg)
    msg = str(ei.value)
    assert "embedding.provider" in msg
    assert "missing" in msg.lower()


def test_load_settings_with_blank_provider_is_treated_as_missing(tmp_path: Path) -> None:
    """An empty string ``provider: ""`` is rejected as missing."""
    cfg = tmp_path / "blank.yaml"
    cfg.write_text(
        """
knowledge_service: {mode: local}
llm: {provider: ""}
embedding: {provider: openai}
vision_llm: {provider: azure}
vector_store: {backend: chroma}
retrieval: {sparse_backend: bm25}
rerank: {backend: none}
""",
        encoding="utf-8",
    )
    with pytest.raises(SettingsError, match="llm.provider"):
        load_settings(cfg)


def test_validate_settings_rejects_non_positive_top_k() -> None:
    """Sanity checks on numeric fields are enforced by validate_settings."""
    from src.core.settings import (
        EmbeddingConfig,
        KnowledgeServiceConfig,
        LLMConfig,
        ObservabilityConfig,
        RetrievalConfig,
        RerankConfig,
        VectorStoreConfig,
        VisionLLMConfig,
    )

    bad = Settings(
        knowledge_service=KnowledgeServiceConfig(mode="local"),
        llm=LLMConfig(provider="azure"),
        embedding=EmbeddingConfig(provider="openai"),
        vision_llm=VisionLLMConfig(provider="azure"),
        vector_store=VectorStoreConfig(backend="chroma"),
        retrieval=RetrievalConfig(sparse_backend="bm25", top_k_dense=0),
        rerank=RerankConfig(backend="none"),
        # unused but required
        evaluation=ObservabilityConfig(),  # type: ignore[arg-type]
        observability=ObservabilityConfig(),
        dashboard=ObservabilityConfig(),  # type: ignore[arg-type]
    )
    with pytest.raises(SettingsError, match="retrieval.top_k_dense"):
        validate_settings(bad)


def test_main_runs_against_default_settings() -> None:
    """The skeleton entry must boot successfully on the shipped config."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "main.py"],  # noqa: F821
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"main.py exited {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "skeleton ready" in result.stderr


def test_main_returns_nonzero_on_missing_required_field(tmp_path: Path) -> None:
    """main.py must exit non-zero and log a readable error when settings
    are broken (here: routing it to a temp file missing a required field)."""
    import os
    import subprocess
    import sys as _sys

    cfg = tmp_path / "broken.yaml"
    cfg.write_text(
        """
knowledge_service: {mode: local}
llm: {provider: azure}
embedding: {}
vision_llm: {provider: azure}
vector_store: {backend: chroma}
retrieval: {sparse_backend: bm25}
rerank: {backend: none}
""",
        encoding="utf-8",
    )
    env = {**os.environ, "SETTINGS_PATH": str(cfg)}
    result = subprocess.run(
        [_sys.executable, "main.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode != 0
    assert "embedding.provider" in result.stderr