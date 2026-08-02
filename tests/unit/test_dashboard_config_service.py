from __future__ import annotations

import sys

import pytest

from core.settings import Settings
from observability.dashboard.services import ConfigService
from scripts.start_dashboard import APP_PATH, _streamlit_command


def test_component_summaries_are_complete_and_do_not_expose_secrets() -> None:
    service = ConfigService(_settings())

    summaries = service.component_summaries()

    assert [summary.code for summary in summaries] == [
        "GEN",
        "EMB",
        "SPLIT",
        "RANK",
        "STORE",
        "EVAL",
    ]
    assert summaries[0].provider == "openai"
    assert summaries[0].model == "gpt-4o-mini"
    assert summaries[3].enabled is False
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


def test_launcher_builds_explicit_headless_streamlit_command() -> None:
    command = _streamlit_command(port=8601, address="127.0.0.1")

    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert str(APP_PATH) in command
    assert command[command.index("--server.port") + 1] == "8601"
    assert command[command.index("--server.address") + 1] == "127.0.0.1"
    assert command[command.index("--server.headless") + 1] == "true"


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
        retrieval={"sparse_backend": "bm25"},
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
