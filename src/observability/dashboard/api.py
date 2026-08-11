"""HTTP API for the React Dashboard, layered on top of dashboard.services."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from src.application.ingestion_jobs import DocumentBusyError, IngestionQueueFullError
from src.application.upload_ingestion import (
    MAX_UPLOAD_BYTES,
    UploadIngestionCoordinator,
    UploadRejectedError,
)
from src.core.services import (
    GrepService,
    active_generation_counts,
    build_knowledge_service,
)
from src.core.services.knowledge_service import KnowledgeService
from src.core.settings import Settings
from src.core.trace import SQLiteTraceStore  # noqa: E402  (plan §C2.4)
from src.core.types import (
    EvaluationReport,
    JsonDict,
)
from src.ingestion import build_ingestion_pipeline
from src.ingestion.storage import SQLiteGrepIndex
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.llm.llm_factory import LLMFactory
from src.libs.loader import SQLiteIntegrityStore
from src.libs.loader.format_router import SUPPORTED_EXTENSIONS
from src.libs.reranker.reranker_factory import RerankerFactory
from src.mcp_server.tools import (
    GrepKnowledgeHubTool,
    QueryKnowledgeHubTool,
    ToolArgumentError,
    ToolExecutionError,
)
from src.observability.dashboard._ingestion_helpers import (
    collection_options,
    dashboard_ai_enrichment_default,
)
from src.observability.dashboard.services import (
    ConfigService,
    DataService,
    EvaluationDashboardService,
    HotpotQABenchmarkJob,
    HotpotQABenchmarkJobService,
    IngestionJob,
    IngestionJobService,
    TraceService,
)
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH
from src.observability.ingestion_trace import create_ingestion_trace_collector
from src.observability.logger import get_logger

LOCAL_ORIGIN_PORTS = (5173, 4173, 8501)
LOCAL_HOSTS = ("localhost", "127.0.0.1")
SECRET_FIELD = "api_key"
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
logger = get_logger(__name__)


@dataclass
class AppContext:
    settings_path: Path | None
    config_service: ConfigService
    settings: Settings
    data_service: DataService | None
    evaluation_service: EvaluationDashboardService | None
    benchmark_jobs: HotpotQABenchmarkJobService
    knowledge_service: KnowledgeService | None
    knowledge_factory: Any
    trace_service: TraceService
    ingestion_jobs: IngestionJobService
    ingestion_factory: Any
    upload_root: Path | None
    upload_coordinator: UploadIngestionCoordinator | None
    trace_collector: Any
    grep_service: GrepService | None = None


@dataclass
class DocumentTarget:
    doc_id: str
    source_path: str
    collection: str


def _configured_text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _safe_build(label: str, factory: Any, settings: Settings) -> Any:
    try:
        return factory(settings)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"dashboard api could not build {label}: {exc}") from exc


def _build_trace_store(config_service: ConfigService) -> SQLiteTraceStore:
    """Construct a SQLiteTraceStore pointing at the configured DB path.

    Honours ``observability.trace_db_path`` and the same retention /
    detail knobs the production collector uses (plan §C2.2 + §C2.4).
    """
    observability = config_service.settings.observability
    retention_days = observability.get("retention_days", 90)
    if not isinstance(retention_days, int) or isinstance(retention_days, bool):
        retention_days = 90
    detail = observability.get("detail", "compact")
    if detail not in {"compact", "debug"}:
        detail = "compact"
    return SQLiteTraceStore(
        config_service.trace_db_path(),
        retention_days=retention_days,
        detail=detail,  # type: ignore[arg-type]
        auto_purge=False,
    )


def _safe_resolve_upload_root(settings: Settings) -> Path | None:
    storage = settings.ingestion.get("storage")
    if not isinstance(storage, Mapping):
        return None
    raw = storage.get("upload_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def _resolve_settings_path(settings_path: str | Path | None) -> Path:
    if settings_path is not None:
        return Path(settings_path).expanduser()
    environment_path = os.environ.get("RAG_SETTINGS_PATH", "").strip()
    if environment_path:
        return Path(environment_path).expanduser()
    return DEFAULT_SETTINGS_PATH


def _build_context(
    settings_path: str | Path | None,
    overrides: Mapping[str, Any],
) -> AppContext:
    resolved = _resolve_settings_path(settings_path)
    config_service = overrides.get("config_service") or ConfigService.from_path(resolved)
    settings = config_service.settings
    data_service = overrides.get("data_service")
    if data_service is None and "data_service" not in overrides:
        data_service = _safe_build("data_service", DataService.from_settings, settings)
    evaluation_service = overrides.get("evaluation_service")
    if evaluation_service is None and "evaluation_service" not in overrides:
        evaluation_service = _safe_build(
            "evaluation_service",
            lambda selected: EvaluationDashboardService.from_settings(selected, resolved),
            settings,
        )
    benchmark_jobs = overrides.get("benchmark_jobs") or HotpotQABenchmarkJobService()
    knowledge_service = overrides.get("knowledge_service")
    knowledge_factory = overrides.get("knowledge_factory") or build_knowledge_service
    trace_service = overrides.get("trace_service") or TraceService(_build_trace_store(config_service))
    ingestion_jobs = overrides.get("ingestion_jobs") or IngestionJobService(max_workers=1)
    ingestion_factory = overrides.get("ingestion_factory") or build_ingestion_pipeline
    upload_root_override = overrides.get("upload_root")
    if upload_root_override is not None:
        upload_root = Path(upload_root_override).expanduser()
    elif "upload_root" in overrides:
        upload_root = None
    else:
        upload_root = _safe_resolve_upload_root(settings)
    trace_collector = overrides.get("trace_collector")
    if trace_collector is None and "trace_collector" not in overrides:
        trace_collector = create_ingestion_trace_collector(settings)
    grep_service = overrides.get("grep_service")
    if grep_service is None and "grep_service" not in overrides:
        grep_service = _try_build_grep_service(settings)
    upload_coordinator = overrides.get("upload_coordinator")
    if upload_coordinator is None and "upload_coordinator" not in overrides:
        upload_coordinator = (
            UploadIngestionCoordinator(
                settings,
                upload_root,
                ingestion_factory,
                jobs=ingestion_jobs,
                collector=trace_collector,
            )
            if upload_root is not None
            else None
        )
    return AppContext(
        settings_path=resolved,
        config_service=config_service,
        settings=settings,
        data_service=data_service,
        evaluation_service=evaluation_service,
        benchmark_jobs=benchmark_jobs,
        knowledge_service=knowledge_service,
        knowledge_factory=knowledge_factory,
        trace_service=trace_service,
        ingestion_jobs=ingestion_jobs,
        ingestion_factory=ingestion_factory,
        upload_root=upload_root,
        upload_coordinator=upload_coordinator,
        trace_collector=trace_collector,
        grep_service=grep_service,
    )


def _try_build_grep_service(settings: Settings) -> GrepService | None:
    if settings.grep.get("enabled") is not True:
        return None
    try:
        storage = settings.ingestion.get("storage")
        if not isinstance(storage, Mapping):
            raise ValueError("Missing required setting: ingestion.storage")
        integrity_path = storage.get("integrity_db_path")
        db_path = settings.grep.get("db_path")
        timeout_ms = settings.grep.get("timeout_ms", 1000)
        if not isinstance(integrity_path, str) or not integrity_path.strip():
            raise ValueError("Missing required setting: ingestion.storage.integrity_db_path")
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("Missing required setting: grep.db_path")
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            raise ValueError("Setting grep.timeout_ms must be a positive integer")
        integrity = SQLiteIntegrityStore(
            integrity_path,
            timeout_seconds=timeout_ms / 1000.0,
        )
        index = SQLiteGrepIndex(db_path, timeout_ms=timeout_ms)
        active, counts = active_generation_counts(integrity)
        index.check_ready(active, counts)
        return GrepService(index, integrity)
    except Exception:
        logger.warning("Dashboard grep capability is unavailable", exc_info=True)
        return None


def _cors_origins() -> list[str]:
    origins: list[str] = []
    for host in LOCAL_HOSTS:
        for port in LOCAL_ORIGIN_PORTS:
            origins.append(f"http://{host}:{port}")
    return origins


def create_app(
    settings_path: str | Path | None = None,
    service_overrides: Mapping[str, Any] | None = None,
) -> FastAPI:
    overrides: dict[str, Any] = dict(service_overrides or {})
    context = _build_context(settings_path, overrides)
    state: dict[str, AppContext] = {"context": context}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            context.ingestion_jobs.shutdown(wait=False)
            context.benchmark_jobs.shutdown(wait=False)

    app = FastAPI(
        title="Modular RAG Dashboard API",
        version="0.1.1",
        lifespan=lifespan,
    )
    app.state.ingestion_jobs = context.ingestion_jobs
    app.state.benchmark_jobs = context.benchmark_jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        if request.url.path == "/api/grep":
            return JSONResponse(status_code=400, content={"detail": "invalid grep request"})
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def _attach_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.app_context = state["context"]
        return await call_next(request)

    _register_routes(app, state)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    index_path = WEB_DIST / "index.html"
    assets_path = WEB_DIST / "assets"
    if not index_path.is_file():
        return
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="dashboard-assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    def frontend(requested_path: str) -> FileResponse:
        if requested_path.startswith("api/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        candidate = (WEB_DIST / requested_path).resolve()
        if candidate.is_relative_to(WEB_DIST.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_path)


def _register_routes(app: FastAPI, state: dict[str, AppContext]) -> None:
    def ctx(request: Request) -> AppContext:
        return request.state.app_context

    def require_data_service(current: AppContext) -> DataService:
        if current.data_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="data service is not available",
            )
        return current.data_service

    def require_evaluation_service(current: AppContext) -> EvaluationDashboardService:
        if current.evaluation_service is None:
            try:
                current.evaluation_service = EvaluationDashboardService.from_settings(
                    current.settings,
                    current.settings_path,
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="evaluation service is not available",
                ) from exc
        return current.evaluation_service

    def require_knowledge_service(current: AppContext) -> KnowledgeService:
        if current.knowledge_service is None:
            # ponytail: one local operator; add a startup lock only if concurrent cold queries matter.
            try:
                current.knowledge_service = current.knowledge_factory(current.settings)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="query service is not available",
                ) from exc
        return current.knowledge_service

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:  # type: ignore[no-untyped-def]
        return HealthResponse(status="ok")

    @app.get("/api/overview", response_model=OverviewResponse)
    def overview(request: Request) -> OverviewResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        components = [
            _component_summary(item) for item in current.config_service.component_summaries()
        ]
        collections = _overview_collections(current)
        stats = _overview_stats(current)
        return OverviewResponse(
            components=components,
            collections=collections,
            stats=stats,
            capabilities=CapabilityPayload(grep=current.grep_service is not None),
        )

    @app.get("/api/components/{code}", response_model=ComponentDetailResponse)
    def get_component(code: str, request: Request) -> ComponentDetailResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        try:
            config = current.config_service.component_config(code)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return ComponentDetailResponse(
            code=code.strip().upper(),
            values=_redacted_values(config),
            provider_options=_provider_options(code.strip().upper()),
        )

    @app.put("/api/components/{code}", response_model=ComponentDetailResponse)
    def put_component(
        code: str,
        payload: ComponentUpdatePayload,
        request: Request,
    ) -> ComponentDetailResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        values = dict(payload.values)
        values.pop(SECRET_FIELD, None)
        try:
            current.config_service.update_component(code, values, api_key=payload.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if current.config_service.settings_path is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="component configuration requires a settings file path",
            )
        reloaded = ConfigService.from_path(current.config_service.settings_path)
        current.config_service = reloaded
        current.settings = reloaded.settings
        current.knowledge_service = None
        current.evaluation_service = None
        return ComponentDetailResponse(
            code=code.strip().upper(),
            values=_redacted_values(reloaded.component_config(code)),
            provider_options=_provider_options(code.strip().upper()),
        )

    @app.patch("/api/components/{code}/enabled", response_model=ComponentDetailResponse)
    def patch_component_enabled(
        code: str,
        payload: ComponentEnabledPayload,
        request: Request,
    ) -> ComponentDetailResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        try:
            current.config_service.set_component_enabled(code, payload.enabled)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if current.config_service.settings_path is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="component configuration requires a settings file path",
            )
        reloaded = ConfigService.from_path(current.config_service.settings_path)
        current.config_service = reloaded
        current.settings = reloaded.settings
        current.knowledge_service = None
        current.evaluation_service = None
        return ComponentDetailResponse(
            code=code.strip().upper(),
            values=_redacted_values(reloaded.component_config(code)),
            provider_options=_provider_options(code.strip().upper()),
        )

    @app.post(
        "/api/collections",
        response_model=CollectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def post_collection(
        payload: CollectionPayload,
        request: Request,
    ) -> CollectionResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        try:
            current.config_service.add_collection(payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if current.config_service.settings_path is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="collection creation requires a settings file path",
            )
        reloaded = ConfigService.from_path(current.config_service.settings_path)
        current.config_service = reloaded
        current.settings = reloaded.settings
        return CollectionResponse(
            name=payload.name.strip(),
            collections=reloaded.known_collections(),
        )

    @app.get("/api/documents", response_model=DocumentListResponse)
    def list_documents(  # type: ignore[no-untyped-def]
        request: Request,
        collection: str | None = None,
    ) -> DocumentListResponse:
        current = ctx(request)
        data_service = require_data_service(current)
        try:
            documents = data_service.list_documents(collection)
            stats = data_service.get_collection_stats(collection)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        return DocumentListResponse(
            collection=collection,
            documents=[_document_payload(item) for item in documents],
            stats=_stats_payload(stats),
        )

    @app.post("/api/query", response_model=DashboardQueryResponse)
    def query_knowledge(
        payload: DashboardQueryPayload,
        request: Request,
    ) -> DashboardQueryResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        service = require_knowledge_service(current)
        tool = QueryKnowledgeHubTool(
            lambda: service,
            get_collector=lambda: current.trace_collector,
        )
        try:
            result = tool.call(payload.model_dump())
        except ToolArgumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except ToolExecutionError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=exc.component_code,
            ) from exc
        structured = result.get("structuredContent")
        content = result.get("content")
        if not isinstance(structured, Mapping) or not isinstance(content, list):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="invalid query response",
            )
        return DashboardQueryResponse.model_validate({**structured, "content": content})

    @app.post("/api/grep", response_model=DashboardGrepResponse)
    def grep_knowledge(
        payload: DashboardGrepPayload,
        request: Request,
    ) -> DashboardGrepResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        if current.grep_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="grep_unavailable",
            )
        tool = GrepKnowledgeHubTool(
            lambda: current.grep_service,  # type: ignore[arg-type,return-value]
            get_collector=lambda: current.trace_collector,
        )
        try:
            result = tool.call(payload.model_dump())
        except ToolArgumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except ToolExecutionError as exc:
            if exc.component_code == "grep_unavailable":
                current.grep_service = None
            http_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.component_code == "grep_unavailable"
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            raise HTTPException(status_code=http_status, detail=exc.component_code) from exc
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="invalid grep response",
            )
        return DashboardGrepResponse.model_validate(structured)

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: str, request: Request) -> JsonDict:  # type: ignore[no-untyped-def]
        current = ctx(request)
        data_service = require_data_service(current)
        try:
            return data_service.get_document_detail(doc_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.delete("/api/documents/{doc_id}", response_model=DeleteDocumentResponse)
    def delete_document(  # type: ignore[no-untyped-def]
        doc_id: str, request: Request
    ) -> DeleteDocumentResponse:
        current = ctx(request)
        data_service = require_data_service(current)
        target = _find_document(current, doc_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"document not found: {doc_id}",
            )
        try:
            result = data_service.delete_document(target.source_path, target.collection)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return DeleteDocumentResponse(
            doc_id=doc_id,
            source_path=target.source_path,
            collection=target.collection,
            deleted_chunks=result.deleted_chunks,
            deleted_images=result.deleted_images,
            removed_bm25=result.removed_bm25,
            removed_integrity_record=result.removed_integrity_record,
            errors=list(result.errors),
        )

    @app.get("/api/traces/{trace_type}", response_model=TraceListResponse)
    def list_traces(trace_type: str, request: Request) -> TraceListResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        try:
            snapshot = current.trace_service.read_traces(trace_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return TraceListResponse(
            trace_type=trace_type,
            traces=[_trace_payload(trace) for trace in snapshot.traces],
            malformed_line_count=snapshot.malformed_line_count,
        )

    @app.get("/api/evaluation/options", response_model=EvaluationOptionsResponse)
    def evaluation_options(request: Request) -> EvaluationOptionsResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        evaluation_service = require_evaluation_service(current)
        return EvaluationOptionsResponse(
            backends=evaluation_service.available_backends(),
            golden_test_sets=[str(path) for path in evaluation_service.golden_test_sets()],
        )

    @app.post("/api/evaluation/runs", response_model=EvaluationReportPayload)
    def run_evaluation(  # type: ignore[no-untyped-def]
        payload: EvaluationRunPayload,
        request: Request,
    ) -> EvaluationReportPayload:
        current = ctx(request)
        evaluation_service = require_evaluation_service(current)
        try:
            report: EvaluationReport = evaluation_service.run(
                payload.test_set_path, payload.backends
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return EvaluationReportPayload(
            run_id=report.run_id,
            metrics=dict(report.metrics),
            cases=list(report.cases),
            metadata=dict(report.metadata),
        )

    @app.get(
        "/api/evaluation/benchmarks/hotpotqa",
        response_model=HotpotQABenchmarkSummaryPayload,
    )
    def hotpotqa_benchmark_summary(  # type: ignore[no-untyped-def]
        request: Request,
    ) -> HotpotQABenchmarkSummaryPayload:
        summary = require_evaluation_service(ctx(request)).hotpotqa_summary()
        return HotpotQABenchmarkSummaryPayload.model_validate(summary)

    @app.post(
        "/api/evaluation/benchmarks/hotpotqa",
        response_model=HotpotQABenchmarkJobPayload,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_hotpotqa_benchmark(  # type: ignore[no-untyped-def]
        payload: HotpotQABenchmarkRunPayload,
        request: Request,
    ) -> HotpotQABenchmarkJobPayload:
        current = ctx(request)
        try:
            job = current.benchmark_jobs.submit(
                require_evaluation_service(current),
                include_images=payload.include_images,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return _benchmark_job_payload(job)

    @app.get(
        "/api/evaluation/benchmarks/hotpotqa/run",
        response_model=HotpotQABenchmarkJobPayload | None,
    )
    def current_hotpotqa_benchmark(  # type: ignore[no-untyped-def]
        request: Request,
    ) -> HotpotQABenchmarkJobPayload | None:
        job = ctx(request).benchmark_jobs.current()
        return _benchmark_job_payload(job) if job is not None else None

    @app.get("/api/ingestion/options", response_model=IngestionOptionsResponse)
    def ingestion_options(request: Request) -> IngestionOptionsResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        data_service = require_data_service(current)
        indexed = data_service.list_collections()
        options = collection_options(current.settings, indexed)
        loader_config = current.settings.ingestion.get("loader", {})
        if not isinstance(loader_config, Mapping):
            loader_config = {}
        vision_config = current.settings.vision_llm
        if not isinstance(vision_config, Mapping):
            vision_config = current.settings.llm
        return IngestionOptionsResponse(
            collections=options,
            ai_enrichment_default=dashboard_ai_enrichment_default(current.settings),
            accepted_extensions=list(SUPPORTED_EXTENSIONS),
            max_upload_bytes=MAX_UPLOAD_BYTES,
            pdf_loader_provider=_configured_text(loader_config, "provider") or "markitdown",
            image_caption_provider=_configured_text(vision_config, "provider"),
            image_caption_model=_configured_text(vision_config, "model"),
            splitter_provider=_configured_text(current.settings.splitter, "provider"),
        )

    @app.post(
        "/api/ingestion/jobs",
        response_model=IngestionJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_ingestion(  # type: ignore[no-untyped-def]
        request: Request,
        file: UploadFile = File(...),
        collection: str = Form(...),
        force: bool = Form(False),
        ai_enrichment: bool = Form(...),
    ) -> IngestionJobResponse:
        current = ctx(request)
        return await _submit_ingestion(
            current,
            file,
            collection=collection,
            force=force,
            ai_enrichment=ai_enrichment,
        )

    @app.get("/api/ingestion/jobs/active", response_model=IngestionJobResponse | None)
    def latest_active_job(request: Request) -> IngestionJobResponse | None:  # type: ignore[no-untyped-def]
        current = ctx(request)
        job = current.ingestion_jobs.latest_active()
        if job is None:
            return None
        return _job_payload(job)

    @app.get("/api/ingestion/jobs/{job_id}", response_model=IngestionJobResponse)
    def get_ingestion_job(job_id: str, request: Request) -> IngestionJobResponse:  # type: ignore[no-untyped-def]
        current = ctx(request)
        try:
            job = current.ingestion_jobs.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return _job_payload(job)


async def _submit_ingestion(  # type: ignore[no-untyped-def]
    current: AppContext,
    file: UploadFile,
    *,
    collection: str,
    force: bool,
    ai_enrichment: bool,
) -> IngestionJobResponse:
    cleaned_collection = collection.strip()
    if not cleaned_collection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="collection must not be empty",
        )
    if current.upload_coordinator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingestion.storage.upload_root is not configured",
        )
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded file is required",
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        job = current.upload_coordinator.submit(
            filename=file.filename,
            content=content,
            collection=cleaned_collection,
            force=force,
            ai_enrichment=ai_enrichment,
        )
    except UploadRejectedError as exc:
        http_status = (
            status.HTTP_409_CONFLICT
            if exc.code == "document_busy"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=http_status, detail=exc.code) from exc
    except DocumentBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="document_busy",
        ) from exc
    except IngestionQueueFullError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="ingestion_queue_full",
        ) from exc

    return _job_payload(job)


def _component_summary(summary: Any) -> ComponentSummaryPayload:
    details = [{"label": label, "value": value} for label, value in summary.details]
    return ComponentSummaryPayload(
        code=summary.code,
        label=summary.label,
        provider=summary.provider,
        model=summary.model,
        enabled=summary.enabled,
        details=details,
    )


def _redacted_values(config: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        if key == SECRET_FIELD:
            cleaned[key] = _redact_secret(value)
            continue
        cleaned[key] = value
    return cleaned


def _redact_secret(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("${") and value.endswith("}"):
        return "configured"
    if value:
        return "configured"
    return ""


def _provider_options(code: str) -> list[str]:
    if code == "GEN":
        return LLMFactory.available_providers()
    if code == "EMB":
        return EmbeddingFactory.available_providers()
    if code == "RANK":
        return RerankerFactory.available_backends()
    return []


def _overview_collections(current: AppContext) -> list[CollectionSummary]:
    indexed: list[str] = []
    if current.data_service is not None:
        try:
            indexed = current.data_service.list_collections()
        except Exception:  # noqa: BLE001
            indexed = []
    names = current.config_service.known_collections(indexed)
    return [CollectionSummary(name=name, indexed=name in indexed) for name in names]


def _overview_stats(current: AppContext) -> StatsPayload:
    if current.data_service is None:
        return StatsPayload(name="all", document_count=0, chunk_count=0, image_count=0)
    try:
        stats = current.data_service.get_collection_stats(None)
    except Exception:  # noqa: BLE001
        return StatsPayload(name="all", document_count=0, chunk_count=0, image_count=0)
    return _stats_payload(stats)


def _stats_payload(stats: Any) -> StatsPayload:
    return StatsPayload(
        name=stats.name,
        document_count=stats.document_count,
        chunk_count=stats.chunk_count,
        image_count=stats.image_count,
    )


def _document_payload(document: Any) -> DocumentPayload:
    metadata = dict(document.metadata)
    return DocumentPayload(
        doc_id=document.doc_id,
        source_path=document.source_path,
        title=document.title,
        summary=document.summary,
        tags=list(document.tags),
        metadata=metadata,
    )


def _find_document(current: AppContext, doc_id: str) -> DocumentTarget | None:
    normalized = doc_id.strip()
    if not normalized or current.data_service is None:
        return None
    try:
        documents = current.data_service.list_documents(None)
    except Exception:  # noqa: BLE001
        return None
    for document in documents:
        if document.doc_id != normalized:
            continue
        collection = document.metadata.get("collection")
        if not isinstance(collection, str) or not collection.strip():
            continue
        return DocumentTarget(
            doc_id=document.doc_id,
            source_path=document.source_path,
            collection=collection,
        )
    return None


def _trace_payload(trace: Any) -> TracePayload:
    return TracePayload(
        trace_id=trace.trace_id,
        trace_type=trace.trace_type,
        started_at=trace.started_at,
        finished_at=trace.finished_at,
        total_elapsed_ms=trace.total_elapsed_ms,
        status=trace.status,
        stages=[_stage_payload(stage) for stage in trace.stages],
        metadata=dict(trace.metadata),
    )


def _stage_payload(stage: Any) -> TraceStagePayload:
    return TraceStagePayload(
        name=stage.name,
        timestamp=stage.timestamp,
        elapsed_ms=stage.elapsed_ms,
        method=stage.method,
        provider=stage.provider,
        details=dict(stage.details),
        data=dict(stage.data),
    )


def _job_payload(job: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        job_id=job.job_id,
        source_path=job.source_path,
        collection=job.collection,
        status=job.status,
        stage=job.stage,
        step=job.step,
        total=job.total,
        active=job.active,
        result=job.result.to_dict() if job.result is not None else None,
        error=job.error,
        started_at=job.started_at,
    )


def _benchmark_job_payload(job: HotpotQABenchmarkJob) -> HotpotQABenchmarkJobPayload:
    return HotpotQABenchmarkJobPayload(
        status=job.status,
        active=job.active,
        include_images=job.include_images,
        started_at=job.started_at,
        finished_at=job.finished_at,
        report=(
            HotpotQABenchmarkReportPayload.model_validate(job.report)
            if job.report is not None
            else None
        ),
        error=job.error,
    )


class HealthResponse(BaseModel):
    status: str = "ok"


class ComponentSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    provider: str
    model: str | None = None
    enabled: bool
    details: list[dict[str, str]] = Field(default_factory=list)


class CapabilityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grep: bool = False


class OverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    components: list[ComponentSummaryPayload]
    collections: list[CollectionSummary]
    stats: StatsPayload
    capabilities: CapabilityPayload


class CollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    indexed: bool


class StatsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    document_count: int
    chunk_count: int
    image_count: int


class ComponentDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    values: dict[str, Any]
    provider_options: list[str] = Field(default_factory=list)


class ComponentUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]
    api_key: str | None = None


class ComponentEnabledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class CollectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class CollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    collections: list[str]


class DocumentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_path: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: str | None
    documents: list[DocumentPayload]
    stats: StatsPayload


class DashboardQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    collection: str = Field(default="default", min_length=1, max_length=128)
    top_k: int = Field(default=5, ge=1, le=20)


class DashboardQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[JsonDict]
    content: list[JsonDict]
    request_id: str | None = None
    trace_id: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class DashboardGrepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=3, max_length=4000)
    collection: str = Field(default="default", min_length=1, max_length=128)
    top_k: int = Field(default=20, ge=1, le=20, strict=True)
    case_sensitive: bool = Field(default=False, strict=True)


class DashboardGrepMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    source: str
    page: int | None = None
    metadata: JsonDict = Field(default_factory=dict)
    match_count: int = Field(ge=1)


class DashboardGrepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[DashboardGrepMatch]
    truncated: bool
    timed_out: bool
    trace_id: str | None = None


class DeleteDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_path: str
    collection: str
    deleted_chunks: int
    deleted_images: int
    removed_bm25: bool
    removed_integrity_record: bool
    errors: list[str]


class TraceStagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    timestamp: str
    elapsed_ms: float | None
    method: str
    provider: str
    details: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class TracePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    trace_type: str
    started_at: str
    finished_at: str | None
    total_elapsed_ms: float
    status: str
    stages: list[TraceStagePayload]
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_type: str
    traces: list[TracePayload]
    malformed_line_count: int


class EvaluationOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backends: list[str]
    golden_test_sets: list[str]


class EvaluationRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_set_path: str
    backends: list[str]


class EvaluationReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    metrics: dict[str, float]
    cases: list[JsonDict]
    metadata: dict[str, Any] = Field(default_factory=dict)


class HotpotQABenchmarkSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    corpus_count: int = Field(ge=0)
    query_count: int = Field(ge=0)
    available: bool


class HotpotQABenchmarkRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_images: bool = True


class BenchmarkMetricsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(ge=0)
    hit_at_5: float = Field(ge=0, le=1)
    mrr_at_5: float = Field(ge=0, le=1)
    misses: list[str]


class BenchmarkEmbeddingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None
    model: str | None
    dimension: int = Field(ge=0)


class BenchmarkGatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str
    min_hit_at_5: float = Field(ge=0, le=1)
    min_mrr_at_5: float = Field(ge=0, le=1)
    min_image_hit_at_5: float = Field(ge=0, le=1)


class HotpotQABenchmarkReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    embedding: BenchmarkEmbeddingPayload
    retrieval: JsonDict
    chunk_count: int = Field(ge=0)
    strategies: dict[str, BenchmarkMetricsPayload]
    image_cases: BenchmarkMetricsPayload
    gate: BenchmarkGatePayload
    passed: bool


class HotpotQABenchmarkJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    active: bool
    include_images: bool
    started_at: float | None
    finished_at: float | None
    report: HotpotQABenchmarkReportPayload | None
    error: str | None


class IngestionOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collections: list[str]
    ai_enrichment_default: bool
    accepted_extensions: list[str]
    max_upload_bytes: int
    pdf_loader_provider: str
    image_caption_provider: str | None
    image_caption_model: str | None
    splitter_provider: str | None


class IngestionJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    source_path: str
    collection: str
    status: str
    stage: str
    step: int
    total: int
    active: bool
    result: dict[str, Any] | None
    error: str | None
    started_at: float | None


def run(app: FastAPI, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


__all__ = [
    "AppContext",
    "IngestionJobResponse",
    "create_app",
    "run",
]
