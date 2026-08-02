from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_dashboard_default_page_renders_configuration_and_asset_stats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
  model: gpt-test
embedding:
  provider: openai
  model: embed-test
splitter:
  provider: recursive
  chunk_size: 500
  chunk_overlap: 50
vector_store:
  backend: chroma
  persist_path: {tmp_path / "chroma"}
  collection_name: dashboard-test
  distance_metric: cosine
retrieval:
  sparse_backend: bm25
rerank:
  backend: none
evaluation:
  backends: [custom]
observability:
  enabled: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RAG_SETTINGS_PATH", str(settings_path))
    app_path = (
        Path(__file__).resolve().parents[2] / "src" / "observability" / "dashboard" / "app.py"
    )

    app = AppTest.from_file(str(app_path), default_timeout=20).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["系统总览"]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Collection", "dashboard-test"),
        ("Documents", "0"),
        ("Chunks", "0"),
        ("Images", "0"),
    ]
    assert [code.value for code in app.code] == [
        "openai / gpt-test",
        "openai / embed-test",
        "recursive",
        "none",
        "chroma / dashboard-test",
        "custom",
    ]
    assert [button.key for button in app.button] == [
        "overview_create_collection",
        "configure_GEN",
        "configure_EMB",
        "configure_SPLIT",
        "configure_RANK",
        "configure_STORE",
        "configure_EVAL",
    ]

    app.button[2].click().run()

    assert not app.exception
    assert app.subheader[-1].value == "Embedding"
    assert [(field.label, field.value) for field in app.selectbox] == [
        ("Collection", "全部"),
        ("Provider", "openai"),
    ]
    assert [(field.label, field.value) for field in app.text_input[1:3]] == [
        ("Model", "embed-test"),
        ("Base URL", ""),
    ]
    assert app.button[-1].label == "保存配置"
