from __future__ import annotations

import time
from pathlib import Path

import pymupdf
from streamlit.testing.v1 import AppTest


def test_dashboard_submits_upload_in_background_and_renders_completion(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path)
    pdf_bytes = _pdf_bytes(tmp_path / "source.pdf", "Background ingestion stays responsive.")
    script = (
        "from src.observability.dashboard.pages.ingestion_manager import render\n"
        f"render(settings_path={str(settings_path)!r})\n"
    )
    app = AppTest.from_string(script, default_timeout=20).run()

    assert not app.exception
    assert app.toggle[0].label == "AI 增强"
    assert app.toggle[0].value is False

    app.file_uploader[0].upload("guide.pdf", pdf_bytes, "application/pdf").run()
    next(button for button in app.button if button.label == "开始摄取").click().run()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not app.success:
        time.sleep(0.05)
        app.run()

    assert not app.exception
    assert [message.value for message in app.success] == ["摄取完成：1 Chunks，0 Images"]
    assert (tmp_path / "data/uploads/guide.pdf").read_bytes() == pdf_bytes
    assert (tmp_path / "logs/traces.jsonl").is_file()


def _pdf_bytes(path: Path, text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path.read_bytes()


def _write_settings(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    content = f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
embedding:
  provider: hash
  dimension: 64
splitter:
  provider: recursive
  chunk_size: 300
  chunk_overlap: 30
ingestion:
  claim_lease_seconds: 1
  batch_size: 2
  storage:
    integrity_db_path: {data / "db/integrity.db"}
    bm25_path: {data / "db/bm25"}
    image_db_path: {data / "db/images.db"}
    image_root: {data / "images"}
    upload_root: {data / "uploads"}
  chunk_refiner:
    use_llm: true
  metadata_enricher:
    use_llm: true
  image_captioner:
    enabled: true
vector_store:
  backend: chroma
  persist_path: {data / "db/chroma"}
  collection_name: dashboard-background
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
observability:
  enabled: true
  log_file: {tmp_path / "logs/traces.jsonl"}
dashboard:
  ingestion_ai_enrichment_default: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path
