from pathlib import Path

import pytest

from core.settings import Settings, load_settings, resolve_settings_path
from main import main

VALID_SETTINGS = """\
knowledge_service:
  mode: local
llm:
  provider: openai
embedding:
  provider: openai
splitter:
  provider: recursive
vector_store:
  backend: chroma
retrieval:
  sparse_backend: bm25
rerank:
  backend: none
evaluation:
  backends: [custom]
observability:
  enabled: true
"""


def write_settings(path: Path, content: str = VALID_SETTINGS) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_settings_returns_validated_dataclass(tmp_path: Path) -> None:
    path = write_settings(tmp_path / "settings.yaml")

    settings = load_settings(str(path))

    assert isinstance(settings, Settings)
    assert settings.knowledge_service["mode"] == "local"
    assert settings.embedding["provider"] == "openai"
    assert settings.ingestion == {}


def test_load_settings_reads_optional_ingestion_section(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path / "settings.yaml",
        VALID_SETTINGS + "ingestion:\n  chunk_refiner:\n    use_llm: true\n",
    )

    settings = load_settings(str(path))

    assert settings.ingestion["chunk_refiner"]["use_llm"] is True


def test_load_settings_reads_optional_vision_llm_section(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path / "settings.yaml",
        VALID_SETTINGS + "vision_llm:\n  provider: azure\n  model: gpt-4o\n",
    )

    settings = load_settings(str(path))

    assert settings.vision_llm == {"provider": "azure", "model": "gpt-4o"}


def test_load_settings_reads_optional_dashboard_section(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path / "settings.yaml",
        VALID_SETTINGS + "dashboard:\n  enabled: true\n  port: 8601\n",
    )

    settings = load_settings(str(path))

    assert settings.dashboard == {"enabled": True, "port": 8601}


def test_load_settings_names_missing_nested_field(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path / "settings.yaml",
        VALID_SETTINGS.replace("embedding:\n  provider: openai\n", "embedding:\n"),
    )

    with pytest.raises(ValueError, match=r"embedding\.provider"):
        load_settings(str(path))


def test_main_loads_settings_at_startup(tmp_path: Path) -> None:
    path = write_settings(tmp_path / "settings.yaml")

    assert isinstance(main(str(path)), Settings)


def test_load_settings_expands_environment_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TEST_LLM_MODEL", "MiniMax-M3")
    config = VALID_SETTINGS.replace(
        "  provider: openai\nembedding:",
        "  provider: openai\n  model: ${TEST_LLM_MODEL}\nembedding:",
        1,
    )
    path = write_settings(tmp_path / "settings.yaml", config)

    assert load_settings(str(path)).llm["model"] == "MiniMax-M3"


def test_resolve_settings_path_uses_explicit_then_environment_then_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    default_path = tmp_path / "default.yaml"
    environment_path = tmp_path / "environment.yaml"
    explicit_path = tmp_path / "explicit.yaml"

    monkeypatch.setenv("RAG_SETTINGS_PATH", str(environment_path))
    assert resolve_settings_path(explicit_path, default_path=default_path) == explicit_path
    assert resolve_settings_path(None, default_path=default_path) == environment_path

    monkeypatch.delenv("RAG_SETTINGS_PATH")
    assert resolve_settings_path(None, default_path=default_path) == default_path
