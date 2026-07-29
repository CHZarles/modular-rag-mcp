from pathlib import Path

import pytest

from core.settings import Settings, load_settings
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


def test_load_settings_expands_environment_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEST_LLM_MODEL", "MiniMax-M3")
    config = VALID_SETTINGS.replace(
        "  provider: openai\nembedding:",
        "  provider: openai\n  model: ${TEST_LLM_MODEL}\nembedding:",
        1,
    )
    path = write_settings(tmp_path / "settings.yaml", config)

    assert load_settings(str(path)).llm["model"] == "MiniMax-M3"
