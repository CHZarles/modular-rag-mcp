"""Unit tests for the FastAPI dashboard API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from core.services.grep_service import GrepMatch, GrepResponse
from core.settings import Settings
from core.types import (
    CollectionInfo,
    DeleteResult,
    DocumentSummary,
    EvaluationReport,
    IngestionRequest,
    IngestionResult,
    JsonDict,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from observability.dashboard.api import create_app
from observability.dashboard.services import (
    ConfigService,
    HotpotQABenchmarkJob,
    IngestionJobService,
    TraceService,
)
from src.ingestion.storage.sqlite_grep_index import GrepIndexUnavailableError


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
    benchmark_summary: JsonDict = field(
        default_factory=lambda: {
            "dataset": "hotpotqa",
            "corpus_count": 1194,
            "query_count": 120,
            "available": True,
        }
    )
    benchmark_report: JsonDict | None = None
    benchmark_calls: list[bool] = field(default_factory=list)

    def available_backends(self) -> list[str]:
        return list(self.backends)

    def golden_test_sets(self) -> list[Path]:
        return list(self.test_sets)

    def run(self, test_set_path: str | Path, backends: list[str]) -> EvaluationReport:
        if self.report is None:
            raise ValueError("no backend selected")
        return self.report

    def hotpotqa_summary(self) -> JsonDict:
        return dict(self.benchmark_summary)

    def run_hotpotqa_benchmark(self, *, include_images: bool = True) -> JsonDict:
        self.benchmark_calls.append(include_images)
        if self.benchmark_report is None:
            raise RuntimeError("benchmark unavailable")
        return dict(self.benchmark_report)


@dataclass
class FakeBenchmarkJobService:
    job: HotpotQABenchmarkJob | None = None

    def submit(
        self,
        evaluation: FakeEvaluationService,
        *,
        include_images: bool,
    ) -> HotpotQABenchmarkJob:
        self.job = HotpotQABenchmarkJob(
            include_images=include_images,
            status="success",
            report=evaluation.run_hotpotqa_benchmark(include_images=include_images),
        )
        return self.job

    def current(self) -> HotpotQABenchmarkJob | None:
        return self.job

    def shutdown(self, *, wait: bool = True) -> None:
        del wait


@dataclass
class FakeKnowledgeService:
    response: QueryResponse
    requests: list[QueryRequest] = field(default_factory=list)

    def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
        self.requests.append(request)
        return self.response

    def list_collections(self) -> list[CollectionInfo]:
        return []

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        raise KeyError(doc_id)


@dataclass
class FakeGrepService:
    calls: list[JsonDict] = field(default_factory=list)

    def search(self, **kwargs: Any) -> GrepResponse:
        self.calls.append(dict(kwargs))
        return GrepResponse(
            matches=[
                GrepMatch(
                    chunk_id="chunk-1",
                    text="exact needle",
                    source_path="/private/manual.pdf",
                    page=2,
                    metadata={"collection": "docs", "private": "drop"},
                    match_count=1,
                )
            ],
            truncated=True,
        )


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
    benchmark_jobs = overrides.pop("benchmark_jobs", FakeBenchmarkJobService())
    ingestion_jobs = overrides.pop("ingestion_jobs", IngestionJobService(max_workers=1))
    config_service = overrides.pop("config_service", _config_service(tmp_path))
    factory = overrides.pop("ingestion_factory", lambda settings: _NoopIngestion())
    return create_app(
        settings_path=tmp_path / "settings.yaml",
        service_overrides={
            "config_service": config_service,
            "data_service": data_service,
            "evaluation_service": evaluation_service,
            "benchmark_jobs": benchmark_jobs,
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


def test_query_endpoint_reuses_knowledge_service_and_returns_public_citations(
    tmp_path: Path,
) -> None:
    service = FakeKnowledgeService(
        QueryResponse(
            results=[
                RetrievalCandidate(
                    chunk_id="chunk-1",
                    text="Generation fencing prevents stale publication.",
                    metadata={"source_path": "/private/manual.pdf", "page": 3},
                    score=0.9,
                    source="fusion",
                    rank=1,
                )
            ]
        )
    )
    client = _client(tmp_path, knowledge_service=service)

    response = client.post(
        "/api/query",
        json={"query": "generation fence", "collection": "docs", "top_k": 3},
    )

    assert response.status_code == 200, response.text
    assert [request.to_dict() for request in service.requests] == [
        QueryRequest(query="generation fence", collection="docs", top_k=3).to_dict()
    ]
    payload = response.json()
    assert payload["results"][0]["source"] == "manual.pdf"
    assert payload["results"][0]["page"] == 3
    assert payload["results"][0]["text"].startswith("Generation fencing")
    assert isinstance(payload["trace_id"], str)


def test_query_endpoint_reports_unavailable_service_without_internal_details(
    tmp_path: Path,
) -> None:
    def unavailable_factory(settings: Settings) -> None:
        del settings
        raise RuntimeError("/private/provider.env is missing")

    client = _client(tmp_path, knowledge_factory=unavailable_factory)

    response = client.post("/api/query", json={"query": "test"})

    assert response.status_code == 503
    assert response.json() == {"detail": "query service is not available"}


def test_grep_endpoint_uses_independent_service_and_public_response(tmp_path: Path) -> None:
    service = FakeGrepService()
    client = _client(tmp_path, grep_service=service)

    response = client.post(
        "/api/grep",
        json={
            "pattern": " needle ",
            "collection": "docs",
            "top_k": 20,
            "case_sensitive": True,
        },
    )

    assert response.status_code == 200, response.text
    assert service.calls == [
        {
            "pattern": " needle ",
            "collection": "docs",
            "top_k": 20,
            "case_sensitive": True,
        }
    ]
    assert response.json()["matches"][0] == {
        "chunk_id": "chunk-1",
        "text": "exact needle",
        "source": "manual.pdf",
        "page": 2,
        "metadata": {"collection": "docs"},
        "match_count": 1,
    }


def test_grep_endpoint_isolated_when_capability_is_unavailable(tmp_path: Path) -> None:
    client = _client(tmp_path, grep_service=None)

    response = client.post("/api/grep", json={"pattern": "needle"})

    assert response.status_code == 503
    assert response.json() == {"detail": "grep_unavailable"}
    assert client.get("/api/overview").json()["capabilities"] == {"grep": False}


@pytest.mark.parametrize(
    "payload",
    [
        {"pattern": "abc", "top_k": True},
        {"pattern": "abc", "case_sensitive": 1},
        {"pattern": "abc", "extra": True},
    ],
)
def test_grep_endpoint_rejects_invalid_json_boundaries_with_400(
    tmp_path: Path,
    payload: JsonDict,
) -> None:
    client = _client(tmp_path, grep_service=FakeGrepService())

    response = client.post("/api/grep", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid grep request"}


def test_grep_endpoint_returns_stable_500_without_internal_details(tmp_path: Path) -> None:
    service = FakeGrepService()

    def fail(**kwargs: Any) -> GrepResponse:
        del kwargs
        raise RuntimeError("/private/index.db exploded")

    service.search = fail  # type: ignore[method-assign]
    client = _client(tmp_path, grep_service=service)

    response = client.post("/api/grep", json={"pattern": "needle"})

    assert response.status_code == 500
    assert response.json() == {"detail": "grep_failed"}
    assert "/private/index.db" not in response.text


def test_runtime_grep_unavailability_turns_off_only_its_capability(tmp_path: Path) -> None:
    grep_service = FakeGrepService()

    def unavailable(**kwargs: Any) -> GrepResponse:
        del kwargs
        raise GrepIndexUnavailableError("missing index")

    grep_service.search = unavailable  # type: ignore[method-assign]
    knowledge_service = FakeKnowledgeService(QueryResponse(results=[]))
    client = _client(
        tmp_path,
        grep_service=grep_service,
        knowledge_service=knowledge_service,
    )

    grep_response = client.post("/api/grep", json={"pattern": "needle"})
    query_response = client.post("/api/query", json={"query": "still available"})

    assert grep_response.status_code == 503
    assert grep_response.json() == {"detail": "grep_unavailable"}
    assert client.get("/api/overview").json()["capabilities"] == {"grep": False}
    assert query_response.status_code == 200


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
    assert codes == ["GEN", "EMB", "SPLIT", "RET", "RANK", "STORE"]
    assert {entry["name"] for entry in payload["collections"]} >= {"default"}
    assert payload["stats"]["document_count"] == 1
    assert payload["capabilities"] == {"grep": False}


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


def test_put_component_ignores_redacted_api_key_in_values(tmp_path: Path) -> None:
    config_service = _config_service(tmp_path)
    settings_path = config_service.settings_path
    assert settings_path is not None
    client = _client(tmp_path, config_service=config_service)

    response = client.put(
        "/api/components/GEN",
        json={
            "values": {
                "provider": "minimax",
                "model": "MiniMax-M3",
                "base_url": "https://api.minimaxi.com/v1",
                "timeout_seconds": 60,
                "api_key": "configured",
            },
            "api_key": "new-secret",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["values"]["api_key"] == "configured"
    secrets_path = settings_path.with_name("secrets.local.yaml")
    assert "new-secret" in secrets_path.read_text(encoding="utf-8")


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
    # Plan §C2.4: SQLite is the read source; the API filters by trace_type
    # and always reports malformed_line_count=0.
    from datetime import datetime, timedelta, timezone

    from src.core.trace import SQLiteTraceStore, TraceContext

    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    base = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    for index, kind in enumerate(("ingestion", "query")):
        trace = TraceContext(trace_type=kind)
        trace.trace_id = f"trace-{kind}"
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

    config_service = _config_service(tmp_path)
    trace_service = TraceService(store)
    client = _client(tmp_path, config_service=config_service, trace_service=trace_service)
    response = client.get("/api/traces/ingestion")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_type"] == "ingestion"
    assert payload["malformed_line_count"] == 0
    assert len(payload["traces"]) == 1
    assert payload["traces"][0]["trace_id"] == "trace-ingestion"


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


def test_hotpotqa_benchmark_endpoints_return_dataset_and_strategy_metrics(
    tmp_path: Path,
) -> None:
    evaluation_service = FakeEvaluationService(
        benchmark_report={
            "dataset": "hotpotqa",
            "embedding": {"provider": "minimax", "model": "embo-01", "dimension": 1536},
            "retrieval": {
                "enable_dense": False,
                "enable_sparse": True,
                "top_k_dense": 20,
                "top_k_sparse": 20,
                "top_k_final": 10,
                "fusion_algorithm": "rrf",
            },
            "chunk_count": 1197,
            "strategies": {
                "bm25": {
                    "case_count": 120,
                    "hit_at_5": 0.9417,
                    "mrr_at_5": 0.8326,
                    "misses": [],
                }
            },
            "image_cases": {
                "case_count": 3,
                "hit_at_5": 1.0,
                "mrr_at_5": 0.8,
                "misses": [],
            },
            "gate": {
                "strategy": "bm25",
                "min_hit_at_5": 0.9,
                "min_mrr_at_5": 0.8,
                "min_image_hit_at_5": 1.0,
            },
            "passed": True,
        }
    )
    client = _client(tmp_path, evaluation_service=evaluation_service)

    summary = client.get("/api/evaluation/benchmarks/hotpotqa")
    started = client.post(
        "/api/evaluation/benchmarks/hotpotqa",
        json={"include_images": False},
    )
    response = client.get("/api/evaluation/benchmarks/hotpotqa/run")

    assert summary.status_code == 200
    assert summary.json()["query_count"] == 120
    assert started.status_code == 202, started.text
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert response.json()["report"]["strategies"]["bm25"]["hit_at_5"] == 0.9417
    assert evaluation_service.benchmark_calls == [False]


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
    assert payload["accepted_extensions"] == [
        ".pdf",
        ".docx",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    ]
    assert payload["max_upload_bytes"] == 50 * 1024 * 1024
    assert payload["pdf_loader_provider"] == "markitdown"
    assert payload["image_caption_provider"] == "openai"
    assert payload["image_caption_model"] == "gpt-4o-mini"
    assert payload["splitter_provider"] == "recursive"


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


def test_dashboard_rejects_same_file_while_ingestion_is_active(tmp_path: Path) -> None:
    class BlockingIngestion(_NoopIngestion):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def ingest(
            self, request: IngestionRequest, on_progress=None, trace=None
        ) -> IngestionResult:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test worker was not released")
            return super().ingest(request, on_progress=on_progress, trace=trace)

    ingestion = BlockingIngestion()
    jobs = IngestionJobService(max_workers=1)
    app = _make_app(
        tmp_path,
        ingestion_jobs=jobs,
        ingestion_factory=lambda _settings: ingestion,
    )
    from fastapi.testclient import TestClient

    client = TestClient(app)
    try:
        first = client.post(
            "/api/ingestion/jobs",
            files={"file": ("guide.pdf", b"%PDF-1.4\nfirst", "application/pdf")},
            data={"collection": "notes", "ai_enrichment": "false"},
        )
        assert first.status_code == 202
        assert ingestion.started.wait(timeout=1)

        second = client.post(
            "/api/ingestion/jobs",
            files={"file": ("guide.pdf", b"%PDF-1.4\nsecond", "application/pdf")},
            data={"collection": "notes", "ai_enrichment": "false"},
        )
        assert second.status_code == 409
        assert second.json()["detail"] == "document_busy"
        assert (tmp_path / "uploads" / "guide.pdf").read_bytes() == b"%PDF-1.4\nfirst"
    finally:
        ingestion.release.set()
        jobs.shutdown()


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
