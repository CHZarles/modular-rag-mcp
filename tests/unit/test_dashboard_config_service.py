from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import Settings
from observability.dashboard.services import ConfigService


def test_component_summaries_are_complete_and_do_not_expose_secrets() -> None:
    service = ConfigService(_settings())

    summaries = service.component_summaries()

    assert [summary.code for summary in summaries] == [
        "GEN",
        "EMB",
        "SPLIT",
        "RET",
        "RANK",
        "STORE",
    ]
    assert summaries[0].provider == "openai"
    assert summaries[0].model == "gpt-4o-mini"
    assert summaries[3].provider == "bm25"
    assert summaries[4].enabled is False
    assert "secret-value" not in repr(summaries)


def test_dashboard_options_are_validated_and_paths_are_project_relative() -> None:
    service = ConfigService(_settings())

    options = service.dashboard_options()

    assert options.enabled is True
    assert options.address == "127.0.0.1"
    assert options.port == 8601
    assert options.traces_dir.is_absolute()
    assert options.traces_dir.name == "logs"
    assert options.refresh_interval == 10
    assert service.trace_path().is_absolute()
    assert service.trace_path().name == "traces.jsonl"


def test_dashboard_options_reject_invalid_port() -> None:
    settings = _settings()
    settings.dashboard["port"] = 70000

    with pytest.raises(ValueError, match="between 1 and 65535"):
        ConfigService(settings).dashboard_options()




def test_component_update_uses_local_override_and_private_secret(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    base_content = _settings_yaml()
    settings_path.write_text(base_content, encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    service.update_component(
        "EMB",
        {
            "provider": "minimax",
            "model": "embo-01",
            "base_url": "https://api.minimax.io/v1",
        },
        api_key="minimax-secret",
    )

    assert settings_path.read_text(encoding="utf-8") == base_content
    local_settings = (tmp_path / "settings.local.yaml").read_text(encoding="utf-8")
    assert "provider: minimax" in local_settings
    assert "minimax-secret" not in local_settings
    secrets_path = tmp_path / "secrets.local.yaml"
    assert secrets_path.stat().st_mode & 0o777 == 0o600
    assert ConfigService.from_path(settings_path).settings.embedding == {
        "provider": "minimax",
        "model": "embo-01",
        "base_url": "https://api.minimax.io/v1",
        "api_key": "minimax-secret",
    }


def test_component_update_rejects_unknown_or_sensitive_public_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(), encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    with pytest.raises(ValueError, match="unsupported embedding settings"):
        service.update_component("EMB", {"api_key": "must-not-be-public"})


def test_component_update_persists_retrieval_experiment_parameters(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(), encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    service.update_component(
        "RET",
        {"enable_dense": True, "top_k_dense": 30, "top_k_final": 5},
    )

    retrieval = ConfigService.from_path(settings_path).settings.retrieval
    assert retrieval["enable_dense"] is True
    assert retrieval["top_k_dense"] == 30
    assert retrieval["top_k_final"] == 5


def test_component_enabled_switch_is_limited_to_optional_components(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(), encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    service.set_component_enabled("GEN", False)

    local_settings = (tmp_path / "settings.local.yaml").read_text(encoding="utf-8")
    assert "llm:" in local_settings
    assert "enabled: false" in local_settings
    assert ConfigService.from_path(settings_path).component_summaries()[0].enabled is False

    with pytest.raises(ValueError, match="must stay enabled"):
        service.set_component_enabled("EMB", False)
    with pytest.raises(ValueError, match="must stay enabled"):
        service.set_component_enabled("EVAL", False)


def test_enabling_reranker_from_none_selects_default_backend(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(), encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    service.set_component_enabled("RANK", True)

    settings = ConfigService.from_path(settings_path).settings
    assert settings.rerank["enabled"] is True
    assert settings.rerank["backend"] == "cross_encoder"


def test_collection_registry_merges_configured_and_indexed_names(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(_settings_yaml(), encoding="utf-8")
    service = ConfigService.from_path(settings_path)

    assert service.known_collections(["papers"]) == ["default", "papers"]

    service.add_collection("notes")

    reloaded = ConfigService.from_path(settings_path)
    assert reloaded.known_collections(["papers"]) == ["default", "notes", "papers"]
    local_settings = (tmp_path / "settings.local.yaml").read_text(encoding="utf-8")
    assert "collections:" in local_settings
    assert "- notes" in local_settings

    with pytest.raises(ValueError, match="collection must be"):
        service.add_collection("../bad")


def _settings_yaml() -> str:
    return """\
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


def _settings() -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "secret-value",
        },
        embedding={
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
        },
        splitter={"provider": "recursive", "chunk_size": 1000, "chunk_overlap": 200},
        vector_store={
            "backend": "chroma",
            "collection_name": "default",
            "distance_metric": "cosine",
        },
        retrieval={
            "enable_dense": False,
            "enable_sparse": True,
            "sparse_backend": "bm25",
            "top_k_dense": 20,
            "top_k_sparse": 20,
            "top_k_final": 10,
        },
        rerank={"backend": "none", "model": "cross-encoder", "top_m": 30},
        evaluation={"backends": ["custom"], "golden_test_set": "tests/golden.json"},
        observability={"enabled": True},
        dashboard={
            "enabled": True,
            "address": "127.0.0.1",
            "port": 8601,
            "traces_dir": "./logs",
            "auto_refresh": True,
            "refresh_interval": 10,
        },
    )
