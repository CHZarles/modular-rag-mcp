from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.settings import Settings
from core.trace import JSONLTraceCollector
from core.types import IngestionRequest, IngestionResult
from observability.ingestion_trace import (
    create_ingestion_trace_collector,
    run_traced_ingestion,
)


class FakeIngestionService:
    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Any = None,
        trace: Any = None,
    ) -> IngestionResult:
        if on_progress is not None:
            on_progress("complete", 7, 7)
        if trace is not None:
            trace.record_stage(
                "ingestion",
                {
                    "method": "ingest",
                    "provider": "FakePipeline",
                    "details": {"status": "success", "chunk_count": 2},
                },
                elapsed_ms=3.0,
            )
        return IngestionResult(
            source_path=request.source_path,
            collection=request.collection,
            status="success",
            file_hash="revision",
            chunk_count=2,
        )


class FailingCollector(JSONLTraceCollector):
    def collect(self, trace: Any) -> None:
        raise OSError("disk full")


def test_run_traced_ingestion_persists_result_metadata_and_stages(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    progress: list[tuple[str, int, int]] = []

    result = run_traced_ingestion(
        FakeIngestionService(),
        IngestionRequest("/tmp/guide.pdf", "docs", force=True),
        JSONLTraceCollector(path),
        lambda stage, step, total: progress.append((stage, step, total)),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result.status == "success"
    assert payload["trace_type"] == "ingestion"
    assert payload["finished_at"] is not None
    assert payload["metadata"] == {
        "source_path": "/tmp/guide.pdf",
        "collection": "docs",
        "force": True,
        "request_id": None,
        "status": "success",
        "chunk_count": 2,
        "image_count": 0,
        "error": None,
    }
    assert payload["stages"][0]["data"]["provider"] == "FakePipeline"
    assert progress == [("complete", 7, 7)]


def test_trace_persistence_failure_does_not_replace_successful_ingestion() -> None:
    result = run_traced_ingestion(
        FakeIngestionService(),
        IngestionRequest("/tmp/guide.pdf", "docs"),
        FailingCollector("unused.jsonl"),
    )

    assert result.status == "success"


def test_create_collector_honors_observability_switch(tmp_path: Path) -> None:
    from src.core.trace import JSONLTraceCollector, SQLiteTraceStore

    sqlite_settings = _settings(
        tmp_path,
        enabled=True,
        backend="sqlite",
        trace_db_path=str(tmp_path / "traces.db"),
        integrity_db_path=str(tmp_path / "integrity.db"),
    )
    jsonl_settings = _settings(
        tmp_path,
        enabled=True,
        backend="jsonl",
        log_file=str(tmp_path / "traces.jsonl"),
    )
    disabled_settings = _settings(tmp_path, enabled=False)

    sqlite_collector = create_ingestion_trace_collector(sqlite_settings)
    jsonl_collector = create_ingestion_trace_collector(jsonl_settings)
    disabled_collector = create_ingestion_trace_collector(disabled_settings)

    assert isinstance(sqlite_collector, SQLiteTraceStore)
    assert sqlite_collector.path == tmp_path / "traces.db"
    assert isinstance(jsonl_collector, JSONLTraceCollector)
    assert jsonl_collector.path == tmp_path / "traces.jsonl"
    assert disabled_collector is None


def _settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    backend: str | None = None,
    log_file: str | None = None,
    trace_db_path: str | None = None,
    integrity_db_path: str | None = None,
) -> Settings:
    observability: dict[str, object] = {"enabled": enabled}
    if backend is not None:
        observability["backend"] = backend
    if log_file is not None:
        observability["log_file"] = log_file
    if trace_db_path is not None:
        observability["trace_db_path"] = trace_db_path
    ingestion: dict[str, object] = {}
    if integrity_db_path is not None:
        ingestion["storage"] = {"integrity_db_path": integrity_db_path}
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability=observability,  # type: ignore[arg-type]
        ingestion=ingestion,  # type: ignore[arg-type]
    )
