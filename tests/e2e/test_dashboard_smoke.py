"""Smoke-test every Dashboard page against populated local storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

from scripts.ingest import main as ingest_main
from src.core.services import build_local_query_engine
from src.core.settings import load_settings
from src.core.types import QueryRequest
from src.libs.embedding import EmbeddingFactory
from src.observability.query_trace import create_trace_collector, run_traced_query

pytestmark = pytest.mark.e2e

PAGES = (
    ("overview", "系统总览"),
    ("data_browser", "数据浏览器"),
    ("ingestion_manager", "Ingestion 管理"),
    ("ingestion_traces", "Ingestion 追踪"),
    ("query_traces", "Query 追踪"),
    ("evaluation_panel", "评估面板"),
)


class DashboardSmokeEmbedding:
    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        return [[float(len(text)), float(sum(text.encode("utf-8")) % 997 + 1)] for text in texts]


def test_all_dashboard_pages_render_with_indexed_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_path = tmp_path / "documents" / "dashboard-guide.pdf"
    _write_pdf(
        document_path,
        "The dashboard displays indexed documents, ingestion traces, and query traces.",
    )
    settings_path = _write_settings(tmp_path)
    monkeypatch.setenv("RAG_SETTINGS_PATH", str(settings_path))
    EmbeddingFactory.register("dashboard-smoke", lambda config: DashboardSmokeEmbedding())
    try:
        assert (
            ingest_main(
                ["--path", str(document_path), "--collection", "docs"],
                settings_path=settings_path,
            )
            == 0
        )
        settings = load_settings(str(settings_path))
        results = run_traced_query(
            build_local_query_engine(settings),
            QueryRequest(query="dashboard traces", collection="docs"),
            create_trace_collector(settings),
        )
        assert results

        rendered: list[str] = []
        for module_name, expected_title in PAGES:
            script = (
                f"from src.observability.dashboard.pages.{module_name} import render\nrender()\n"
            )
            app = AppTest.from_string(script, default_timeout=20).run()

            assert not app.exception, f"{module_name}: {list(app.exception)}"
            assert [title.value for title in app.title] == [expected_title]
            rendered.append(module_name)

        assert rendered == [module_name for module_name, _ in PAGES]
    finally:
        EmbeddingFactory.unregister("dashboard-smoke")


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _write_settings(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    golden_path = Path(__file__).resolve().parents[1] / "fixtures" / "golden_test_set.json"
    content = f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
  model: dashboard-smoke
embedding:
  provider: dashboard-smoke
  model: dashboard-smoke
splitter:
  provider: recursive
  chunk_size: 300
  chunk_overlap: 30
ingestion:
  claim_lease_seconds: 60
  batch_size: 2
  storage:
    integrity_db_path: {data / "db/integrity.db"}
    bm25_path: {data / "db/bm25"}
    image_db_path: {data / "db/images.db"}
    image_root: {data / "images"}
    upload_root: {data / "uploads"}
  chunk_refiner:
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    enabled: false
vector_store:
  backend: chroma
  persist_path: {data / "db/chroma"}
  collection_name: dashboard-smoke
  distance_metric: cosine
retrieval:
  enable_dense: true
  enable_sparse: true
  sparse_backend: bm25
  fusion_algorithm: rrf
  top_k_dense: 5
  top_k_sparse: 5
  top_k_final: 3
rerank:
  backend: none
  top_m: 5
  timeout_seconds: 5
evaluation:
  backends: [custom]
  golden_test_set: {golden_path}
observability:
  enabled: true
  log_file: {data / "logs/traces.jsonl"}
"""
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path
