"""Integration tests for the FastAPI dashboard API end-to-end."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.libs.llm import ChatResponse
from src.observability.dashboard.api import create_app


def test_api_ingestion_job_writes_pdf_and_completes(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path)
    pdf_bytes = _pdf_bytes(tmp_path / "guide.pdf", "HTTP API ingestion run.")
    app = create_app(settings_path=settings_path)
    client = TestClient(app)

    response = client.post(
        "/api/ingestion/jobs",
        files={"file": ("guide.pdf", pdf_bytes, "application/pdf")},
        data={"collection": "api-docs", "force": "false", "ai_enrichment": "false"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    job_id = payload["job_id"]
    assert payload["status"] == "queued"

    deadline = time.monotonic() + 10
    final_status = None
    while time.monotonic() < deadline:
        poll = client.get(f"/api/ingestion/jobs/{job_id}")
        assert poll.status_code == 200
        body = poll.json()
        final_status = body["status"]
        if final_status in {"success", "skipped", "failed"}:
            break
        time.sleep(0.05)

    assert final_status == "success"
    stored = tmp_path / "data" / "uploads" / "guide.pdf"
    assert stored.is_file()
    assert stored.read_bytes() == pdf_bytes

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["stats"]["document_count"] >= 1

    documents = client.get("/api/documents")
    assert documents.status_code == 200
    assert any(
        document["source_path"].endswith("guide.pdf") for document in documents.json()["documents"]
    )


def test_api_health_endpoint_reachable(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path)
    app = create_app(settings_path=settings_path)
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("filename", "expected_text", "doc_type"),
    [
        ("architecture.docx", "DOCX unique architecture phrase", "docx"),
        ("people.csv", "role: Platform Architect", "csv"),
    ],
)
def test_api_ingests_docx_and_csv_through_existing_pipeline(
    tmp_path: Path,
    filename: str,
    expected_text: str,
    doc_type: str,
) -> None:
    client = TestClient(create_app(settings_path=_write_settings(tmp_path)))
    content = (
        _docx_bytes(expected_text)
        if filename.endswith(".docx")
        else b"name,role\nAlice,Platform Architect\n"
    )

    response = client.post(
        "/api/ingestion/jobs",
        files={"file": (filename, content, "application/octet-stream")},
        data={"collection": "api-docs", "force": "false", "ai_enrichment": "false"},
    )

    assert response.status_code == 202, response.text
    assert _wait_for_job(client, response.json()["job_id"])["status"] == "success"
    documents = client.get("/api/documents", params={"collection": "api-docs"}).json()["documents"]
    document = next(item for item in documents if item["source_path"].endswith(filename))
    detail = client.get(f"/api/documents/{document['doc_id']}").json()
    assert any(expected_text in chunk["text"] for chunk in detail["chunks"])
    assert detail["chunks"][0]["metadata"]["doc_type"] == doc_type


def test_api_ingests_and_retrieves_standalone_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVision:
        def chat_with_image(self, _text: str, _image: Any, **_kwargs: Any) -> ChatResponse:
            return ChatResponse(
                content="Three tier ingestion architecture with a format router.",
                model="fake-vision",
            )

    monkeypatch.setattr(
        "src.ingestion.transform.image_captioner.create_vision_llm",
        lambda _settings: FakeVision(),
    )
    client = TestClient(create_app(settings_path=_write_settings(tmp_path)))
    content = _image_bytes()

    response = client.post(
        "/api/ingestion/jobs",
        files={"file": ("architecture.png", content, "image/png")},
        data={"collection": "api-images", "force": "false", "ai_enrichment": "false"},
    )

    assert response.status_code == 202, response.text
    assert _wait_for_job(client, response.json()["job_id"])["status"] == "success"
    documents = client.get("/api/documents", params={"collection": "api-images"}).json()["documents"]
    document = next(item for item in documents if item["source_path"].endswith("architecture.png"))
    detail = client.get(f"/api/documents/{document['doc_id']}").json()
    assert "Three tier ingestion architecture" in detail["chunks"][0]["text"]
    assert len(detail["images"]) == 1
    assert Path(detail["images"][0]["path"]).read_bytes() == content

    query = client.post(
        "/api/query",
        json={"query": "format router architecture", "collection": "api-images", "top_k": 3},
    )
    assert query.status_code == 200, query.text
    assert any(block["type"] == "image" for block in query.json()["content"])

    class FailingVision:
        def chat_with_image(self, _text: str, _image: Any, **_kwargs: Any) -> ChatResponse:
            raise RuntimeError("vision unavailable")

    monkeypatch.setattr(
        "src.ingestion.transform.image_captioner.create_vision_llm",
        lambda _settings: FailingVision(),
    )
    retry = client.post(
        "/api/ingestion/jobs",
        files={"file": ("architecture.png", content, "image/png")},
        data={"collection": "api-images", "force": "true", "ai_enrichment": "false"},
    )
    assert retry.status_code == 202, retry.text
    failed = _wait_for_job(client, retry.json()["job_id"])
    assert failed["status"] == "failed"
    assert "image_caption_required" in failed["error"]
    active_detail = client.get(f"/api/documents/{document['doc_id']}").json()
    assert "Three tier ingestion architecture" in active_detail["chunks"][0]["text"]


def _pdf_bytes(path: Path, text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path.read_bytes()


def _docx_bytes(text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body>"
            "</w:document>",
        )
    return output.getvalue()


def _image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format="PNG")
    return output.getvalue()


def _wait_for_job(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/ingestion/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"success", "skipped", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"ingestion job {job_id} did not finish")


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
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    enabled: false
vector_store:
  backend: chroma
  persist_path: {data / "db/chroma"}
  collection_name: dashboard-api-integration
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
  enabled: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path



def test_api_traces_endpoint_reads_from_sqlite_store(tmp_path: Path) -> None:
    """``/api/traces/{type}`` reads from the configured SQLite store."""
    from datetime import datetime, timedelta, timezone

    from src.core.trace import SQLiteTraceStore, TraceContext

    settings_path = _write_settings(tmp_path)
    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)

    base = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    for index in range(3):
        trace = TraceContext(trace_type="query")
        trace.trace_id = f"trace-{index}"
        trace.started_at = (base + timedelta(minutes=index)).isoformat()
        trace.metadata.update({"status": "success"})
        trace.record_stage(
            "embed",
            {"method": "dense", "provider": "local"},
            elapsed_ms=1.0,
        )
        trace._finish_mono = trace._start_mono + 0.001  # type: ignore[attr-defined]
        trace.finished_at = (base + timedelta(minutes=index, milliseconds=1)).isoformat()
        store.collect(trace)

    app = create_app(
        settings_path=settings_path,
        service_overrides={"trace_service": __import__(
            "src.observability.dashboard.services", fromlist=["TraceService"]
        ).TraceService(store)},
    )
    client = TestClient(app)

    response = client.get("/api/traces/query")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_type"] == "query"
    assert payload["malformed_line_count"] == 0
    assert len(payload["traces"]) == 3
    # Newest first.
    assert [trace["trace_id"] for trace in payload["traces"]] == [
        "trace-2",
        "trace-1",
        "trace-0",
    ]


def test_api_traces_endpoint_rejects_unknown_trace_type(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path)
    app = create_app(settings_path=settings_path)
    client = TestClient(app)

    response = client.get("/api/traces/evaluation")

    assert response.status_code == 400
    assert "evaluation" in response.json()["detail"]
