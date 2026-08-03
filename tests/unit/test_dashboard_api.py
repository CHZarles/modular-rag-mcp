"""Unit tests for the FastAPI dashboard API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.settings import Settings
from core.types import (
    CollectionInfo,
    DeleteResult,
    DocumentSummary,
    EvaluationReport,
    IngestionRequest,
    IngestionResult,
    JsonDict,
)
from observability.dashboard.api import create_app
from observability.dashboard.services import ConfigService, IngestionJobService, TraceService


@dataclass
class FakeDataService:
    documents: list[DocumentSummary] = field(default_factory=list)
    stats: CollectionInfo = field(
        default_factory=lambda: CollectionInfo(
            name="all", document_count=0, chunk_count=0, image_count=0
        )
    )
    detail: JsonDict = field(default_factory=dict)
    deleted: list[tuple[str, str]] = field(default_factory=list)

    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]:
        if collection is None:
            return list(self.documents)
        return [
            document
            for document in self.documents
            if document.metadata.get("collection") == collection
        ]

    def list_collections(self) -> list[str]:
        return sorted(
            {
                str(document.metadata["collection"])
                for document in self.documents
                if document.metadata.get("collection")
            },
            key=str.casefold,
        )

    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo:
        if collection is None:
            return self.stats
        documents = self.list_documents(collection)
        return CollectionInfo(
            name=collection,
            document_count=len(documents),
            chunk_count=sum(int(document.metadata.get("chunk_count", 0)) for document in documents),
            image_count=sum(int(document.metadata.get("image_count", 0)) for document in documents),
        )

    def get_document_detail(self, doc_id: str) -> JsonDict:
        if doc_id not in {document.doc_id for document in self.documents}:
            raise KeyError(f"document not found: {doc_id}")
        return self.detail

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        self.deleted.append((source_path, collection))
        return DeleteResult(
            source_path=source_path,
            collection=collection,
            deleted_chunks=2,
            deleted_images=1,
            removed_bm25=True,
            removed_integrity_record=True,
        )


@dataclass
class FakeEvaluationService:
    backends: list[str] = field(default_factory=lambda: ["custom"])
    test_sets: list[Path] = field(default_factory=list)
    report: EvaluationReport | None = None

    def available_backends(self) -> list[str]:
        return list(self.backends)

    def golden_test_sets(self) -> list[Path]:
        return list(self.test_sets)

    def run(self, test_set_path: str | Path, backends: list[str]) -> EvaluationReport:
        if self.report is None:
            raise ValueError("no backend selected")
        return self.report


def _config_service(tmp_path: Path) -> ConfigService:
    settings_path = tmp_path / "settings.yaml"
    uploads = tmp_path / "uploads"
    settings_path.write_text(_settings_yaml(uploads), encoding="utf-8")
    return ConfigService.from_path(settings_path)


def _settings_yaml(uploads: Path | None = None) -> str:
    upload_section = f"  storage:\n    upload_root: {uploads}\n" if uploads is not None else ""
    return f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
  model: gpt-4o-mini
embedding:
  provider: openai
  model: text-embedding-3-small
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
  enabled: false
ingestion:
{upload_section}"""


def _make_app(tmp_path: Path, **overrides: Any):
    data_service = overrides.pop("data_service", FakeDataService())
    evaluation_service = overrides.pop("evaluation_service", FakeEvaluationService())
    ingestion_jobs = overrides.pop("ingestion_jobs", IngestionJobService(max_workers=1))
    config_service = overrides.pop("config_service", _config_service(tmp_path))
    factory = overrides.pop("ingestion_factory", lambda settings: _NoopIngestion())
    return create_app(
        settings_path=tmp_path / "settings.yaml",
        service_overrides={
            "config_service": config_service,
            "data_service": data_service,
            "evaluation_service": evaluation_service,
            "ingestion_jobs": ingestion_jobs,
            "ingestion_factory": factory,
            "trace_collector": None,
            **overrides,
        },
    )


class _NoopIngestion:
    def __init__(self) -> None:
        self.requests: list[IngestionRequest] = []

    def ingest(self, request: IngestionRequest, on_progress=None, trace=None) -> IngestionResult:
        del on_progress, trace
        self.requests.append(request)
        return IngestionResult(
            source_path=request.source_path,
            collection=request.collection,
            status="success",
            file_hash="hash",
            chunk_count=1,
            image_count=0,
        )


@dataclass
class _Client:
    app: Any

    def __post_init__(self) -> None:
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

    def get(self, path: str, **kwargs: Any):
        return self.client.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any):
        return self.client.post(path, **kwargs)

    def put(self, path: str, **kwargs: Any):
        return self.client.put(path, **kwargs)

    def patch(self, path: str, **kwargs: Any):
        return self.client.patch(path, **kwargs)

    def delete(self, path: str, **kwargs: Any):
        return self.client.delete(path, **kwargs)


def _client(tmp_path: Path, **overrides: Any) -> _Client:
    return _Client(app=_make_app(tmp_path, **overrides))


def test_health_endpoint_returns_ok(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_overview_aggregates_components_collections_and_stats(tmp_path: Path) -> None:
    data_service = FakeDataService(
        documents=[
            DocumentSummary(
                doc_id="doc-a",
                source_path="/tmp/a.pdf",
                metadata={"collection": "notes", "chunk_count": 2, "image_count": 1},
            )
        ],
        stats=CollectionInfo(name="all", document_count=1, chunk_count=2, image_count=1),
    )
    client = _client(tmp_path, data_service=data_service)
    response = client.get("/api/overview")
    assert response.status_code == 200
    payload = response.json()
    codes = [component["code"] for component in payload["components"]]
    assert codes == ["GEN", "EMB", "SPLIT", "RANK", "STORE", "EVAL"]
    assert {entry["name"] for entry in payload["collections"]} >= {"default"}
    assert payload["stats"]["document_count"] == 1


def test_get_component_redacts_api_key(tmp_path: Path) -> None:
    config_service = _config_service(tmp_path)
    config_service.settings.llm["api_key"] = "super-secret-token"
    client = _client(tmp_path, config_service=config_service)
    response = client.get("/api/components/GEN")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "GEN"
    assert payload["values"]["api_key"] != "super-secret-token"
    assert "super-secret-token" not in response.text
    assert "minimax" in payload["provider_options"]


def test_get_component_unknown_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/components/NOPE")
    assert response.status_code == 404


def test_put_component_rejects_unknown_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.put(
        "/api/components/GEN",
        json={"values": {"provider": "openai", "model": "gpt", "bogus": True}, "api_key": None},
    )
    assert response.status_code == 400


def test_patch_component_enabled_toggles_rerank(tmp_path: Path) -> None:
    config_service = _config_service(tmp_path)
    settings_path = config_service.settings_path
    assert settings_path is not None
    client = _client(tmp_path, config_service=config_service)
    response = client.patch("/api/components/RANK/enabled", json={"enabled": True})
    assert response.status_code == 200
    reloaded = ConfigService.from_path(settings_path)
    assert reloaded.settings.rerank["enabled"] is True
    assert reloaded.settings.rerank["backend"] == "cross_encoder"


def test_post_collection_persists_simple_name(tmp_path: Path) -> None:
    config_service = _config_service(tmp_path)
    settings_path = config_service.settings_path
    assert settings_path is not None
    client = _client(tmp_path, config_service=config_service)
    response = client.post("/api/collections", json={"name": "research"})
    assert response.status_code == 201
    assert "research" in response.json()["collections"]
    overview = client.get("/api/overview")
    assert "research" in {item["name"] for item in overview.json()["collections"]}
    reloaded = ConfigService.from_path(settings_path)
    assert reloaded.known_collections() == ["default", "research"]


def test_post_collection_rejects_invalid_name(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/collections", json={"name": "../bad"})
    assert response.status_code == 400


def test_documents_endpoint_returns_payload_and_stats(tmp_path: Path) -> None:
    data_service = FakeDataService(
        documents=[
            DocumentSummary(
                doc_id="doc-a",
                source_path="/tmp/a.pdf",
                metadata={"collection": "notes", "chunk_count": 2, "image_count": 0},
            )
        ],
        stats=CollectionInfo(name="all", document_count=1, chunk_count=2, image_count=0),
    )
    client = _client(tmp_path, data_service=data_service)
    response = client.get("/api/documents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["collection"] is None
    assert payload["documents"][0]["doc_id"] == "doc-a"
    assert payload["stats"]["document_count"] == 1


def test_get_document_detail_propagates_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/documents/missing")
    assert response.status_code == 404


def test_delete_document_resolves_doc_id_to_source_path(tmp_path: Path) -> None:
    data_service = FakeDataService(
        documents=[
            DocumentSummary(
                doc_id="doc-a",
                source_path="/tmp/a.pdf",
                metadata={"collection": "notes"},
            )
        ]
    )
    client = _client(tmp_path, data_service=data_service)
    response = client.delete("/api/documents/doc-a")
    assert response.status_code == 200
    assert data_service.deleted == [("/tmp/a.pdf", "notes")]


def test_delete_missing_document_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.delete("/api/documents/missing")
    assert response.status_code == 404


def test_traces_endpoint_returns_records(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps(_trace_payload()),
                json.dumps(_trace_payload(trace_type="query")),
                "{malformed json",
            ]
        ),
        encoding="utf-8",
    )
    config_service = _config_service(tmp_path)
    trace_service = TraceService(trace_path)
    client = _client(tmp_path, config_service=config_service, trace_service=trace_service)
    response = client.get("/api/traces/ingestion")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_type"] == "ingestion"
    assert payload["malformed_line_count"] == 1
    assert len(payload["traces"]) == 1


def test_evaluation_options_returns_backends_and_sets(tmp_path: Path) -> None:
    evaluation_service = FakeEvaluationService(
        backends=["custom", "ragas"],
        test_sets=[Path("golden.json")],
    )
    client = _client(tmp_path, evaluation_service=evaluation_service)
    response = client.get("/api/evaluation/options")
    assert response.status_code == 200
    assert response.json() == {
        "backends": ["custom", "ragas"],
        "golden_test_sets": ["golden.json"],
    }


def test_evaluation_runs_returns_report(tmp_path: Path) -> None:
    evaluation_service = FakeEvaluationService(
        report=EvaluationReport(
            run_id="run-1",
            metrics={"hit_rate": 1.0},
            cases=[{"case_id": "q1", "query": "question"}],
            metadata={"case_count": 1},
        )
    )
    client = _client(tmp_path, evaluation_service=evaluation_service)
    response = client.post(
        "/api/evaluation/runs",
        json={"test_set_path": "golden.json", "backends": ["custom"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run-1"
    assert payload["metrics"] == {"hit_rate": 1.0}


def test_evaluation_runs_rejects_empty_backends(tmp_path: Path) -> None:
    evaluation_service = FakeEvaluationService()
    client = _client(tmp_path, evaluation_service=evaluation_service)
    response = client.post(
        "/api/evaluation/runs",
        json={"test_set_path": "golden.json", "backends": []},
    )
    assert response.status_code == 400


def test_ingestion_options_lists_collections_and_default(tmp_path: Path) -> None:
    data_service = FakeDataService(
        documents=[
            DocumentSummary(
                doc_id="doc-a",
                source_path="/tmp/a.pdf",
                metadata={"collection": "papers"},
            )
        ]
    )
    client = _client(tmp_path, data_service=data_service)
    response = client.get("/api/ingestion/options")
    assert response.status_code == 200
    payload = response.json()
    assert "papers" in payload["collections"]
    assert payload["ai_enrichment_default"] is False


def test_ingestion_job_endpoint_writes_pdf_and_dispatches(
    tmp_path: Path,
) -> None:
    factory_calls: list[Settings] = []
    captured_jobs = IngestionJobService(max_workers=1)

    def factory(settings: Settings) -> _NoopIngestion:
        factory_calls.append(settings)
        return _NoopIngestion()

    app = _make_app(
        tmp_path,
        ingestion_jobs=captured_jobs,
        ingestion_factory=factory,
    )
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/api/ingestion/jobs",
        files={"file": ("guide.pdf", b"%PDF-1.4\ndemo", "application/pdf")},
        data={"collection": "notes", "force": "false", "ai_enrichment": "true"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["collection"] == "notes"
    assert payload["status"] == "queued"
    stored = tmp_path / "uploads" / "guide.pdf"
    assert stored.is_file()
    assert stored.read_bytes().startswith(b"%PDF-1.4")


def test_ingestion_job_rejects_non_pdf(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/api/ingestion/jobs",
        files={"file": ("guide.txt", b"plain text", "text/plain")},
        data={"collection": "notes", "ai_enrichment": "false"},
    )
    assert response.status_code == 400


def test_ingestion_job_rejects_empty_pdf(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/api/ingestion/jobs",
        files={"file": ("guide.pdf", b"", "application/pdf")},
        data={"collection": "notes", "ai_enrichment": "false"},
    )
    assert response.status_code == 400


def test_get_ingestion_job_returns_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/ingestion/jobs/does-not-exist")
    assert response.status_code == 404


def test_cors_allows_local_vite_origin(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_blocks_remote_origin(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get(
        "/api/health",
        headers={"Origin": "http://evil.example"},
    )
    assert response.headers.get("access-control-allow-origin") is None


def _trace_payload(*, trace_type: str = "ingestion") -> JsonDict:
    return {
        "trace_id": "trace-1",
        "trace_type": trace_type,
        "started_at": "2024-01-01T00:00:00+00:00",
        "finished_at": None,
        "total_elapsed_ms": 10.0,
        "stages": [
            {
                "stage": "load",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "elapsed_ms": 5.0,
                "data": {"method": "load", "provider": "pdf", "details": {}},
            }
        ],
        "metadata": {"source_path": "guide.pdf"},
    }
