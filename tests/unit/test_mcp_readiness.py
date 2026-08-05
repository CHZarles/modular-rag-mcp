"""Unit tests for the MCP readiness probe (plan §5.7 / §C4).

The default probes are intentionally injectable so tests can guarantee no
LLM/Embedding network call happens during readiness (plan §C4 mock proof).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.core.settings import Settings
from src.mcp_server.readiness import (
    STATUS_DEGRADED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    ReadinessCheck,
    ReadinessResult,
    ReadinessService,
)


def _ok(name: str) -> ReadinessCheck:
    return ReadinessCheck(name, "ok")


def _unwritable(name: str) -> ReadinessCheck:
    return ReadinessCheck(name, "unwritable")


def _missing(name: str) -> ReadinessCheck:
    return ReadinessCheck(name, "missing")


@pytest.fixture
def settings_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "knowledge_service:\n  mode: local\n"
        "llm:\n  provider: openai\n"
        "embedding:\n  provider: hash\n  dimension: 8\n"
        "splitter:\n  provider: recursive\n  chunk_size: 32\n  chunk_overlap: 4\n"
        "vector_store:\n  backend: chroma\n  persist_path: /tmp/rag/vector\n"
        "retrieval:\n  sparse_backend: bm25\n  top_k_dense: 5\n  top_k_sparse: 5\n  top_k_final: 3\n"
        "rerank:\n  backend: none\n  top_m: 5\n  timeout_seconds: 5\n"
        "evaluation:\n  backends: [custom]\n"
        "observability:\n  enabled: false\n",
        encoding="utf-8",
    )
    yield path


def _service(
    settings_path: Path,
    *,
    settings: ReadinessCheck | None = None,
    knowledge: ReadinessCheck | None = None,
    trace: ReadinessCheck | None = None,
) -> ReadinessService:
    return ReadinessService(
        settings_path=settings_path,
        settings_probe=lambda: settings or _ok("settings"),
        knowledge_store_probe=lambda _s: knowledge or _ok("knowledge_store"),
        trace_store_probe=lambda _s: trace or _ok("trace_store"),
    )


# --- Aggregation ----------------------------------------------------------


def test_aggregate_returns_ready_when_all_checks_ok(settings_path: Path) -> None:
    result = _service(settings_path).check()

    assert result.status == STATUS_READY
    assert result.http_status == 200
    assert result.to_wire() == {
        "status": "ready",
        "checks": {
            "settings": "ok",
            "knowledge_store": "ok",
            "trace_store": "ok",
        },
    }


def test_aggregate_returns_degraded_when_only_trace_store_fails(
    settings_path: Path,
) -> None:
    result = _service(settings_path, trace=_unwritable("trace_store")).check()

    assert result.status == STATUS_DEGRADED
    assert result.http_status == 200
    assert result.to_wire()["checks"]["trace_store"] == "unwritable"


def test_aggregate_returns_unavailable_when_settings_invalid(
    settings_path: Path,
) -> None:
    result = _service(settings_path, settings=_unwritable("settings")).check()

    assert result.status == STATUS_UNAVAILABLE
    assert result.http_status == 503
    # Knowledge and trace probes are skipped when settings fail fast.
    assert set(result.checks) == {"settings"}


def test_aggregate_returns_unavailable_when_knowledge_store_fails(
    settings_path: Path,
) -> None:
    result = _service(settings_path, knowledge=_unwritable("knowledge_store")).check()

    assert result.status == STATUS_UNAVAILABLE
    assert result.http_status == 503


def test_aggregate_uses_503_when_trace_store_missing(settings_path: Path) -> None:
    # Per §5.7 the missing case is treated like any other trace_store
    # failure — degraded, not unavailable.
    result = _service(settings_path, trace=_missing("trace_store")).check()

    assert result.status == STATUS_DEGRADED
    assert result.http_status == 200


# --- Real default probes --------------------------------------------------


def test_default_knowledge_store_probe_accepts_creatable_paths(
    settings_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash", "dimension": 8},
        splitter={"provider": "recursive", "chunk_size": 32, "chunk_overlap": 4},
        vector_store={"backend": "chroma", "persist_path": str(tmp_path / "vector")},
        retrieval={"sparse_backend": "bm25", "top_k_dense": 5, "top_k_sparse": 5, "top_k_final": 3},
        rerank={"backend": "none", "top_m": 5, "timeout_seconds": 5},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={"integrity_db_path": str(tmp_path / "integrity.db")},
    )

    from src.mcp_server.readiness import _default_knowledge_store_probe

    check = _default_knowledge_store_probe(settings)

    assert check.code == "ok"


def test_default_knowledge_store_probe_reports_unwritable_paths(
    settings_path: Path, tmp_path: Path
) -> None:
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o555)
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash", "dimension": 8},
        splitter={"provider": "recursive", "chunk_size": 32, "chunk_overlap": 4},
        vector_store={"backend": "chroma", "persist_path": str(read_only / "vector")},
        retrieval={"sparse_backend": "bm25", "top_k_dense": 5, "top_k_sparse": 5, "top_k_final": 3},
        rerank={"backend": "none", "top_m": 5, "timeout_seconds": 5},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={"integrity_db_path": str(read_only / "integrity.db")},
    )

    from src.mcp_server.readiness import _default_knowledge_store_probe

    check = _default_knowledge_store_probe(settings)

    assert check.code == "unwritable"
    try:
        read_only.chmod(0o755)  # ensure tmp_path teardown can clean up
    except OSError:  # pragma: no cover
        pass


def test_default_trace_store_probe_uses_sqlite_check_writable(
    settings_path: Path, tmp_path: Path
) -> None:
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash", "dimension": 8},
        splitter={"provider": "recursive", "chunk_size": 32, "chunk_overlap": 4},
        vector_store={"backend": "chroma", "persist_path": str(tmp_path / "vector")},
        retrieval={"sparse_backend": "bm25", "top_k_dense": 5, "top_k_sparse": 5, "top_k_final": 3},
        rerank={"backend": "none", "top_m": 5, "timeout_seconds": 5},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True, "trace_db_path": str(tmp_path / "traces.db")},
    )

    from src.mcp_server.readiness import _default_trace_store_probe

    assert _default_trace_store_probe(settings).code == "ok"


# --- Settings reload failure path -----------------------------------------


def test_check_reports_unavailable_when_settings_file_missing(
    tmp_path: Path,
) -> None:
    service = ReadinessService(
        settings_path=tmp_path / "missing.yaml",
        settings_probe=lambda: _ok("settings"),  # first-pass probe passes
        knowledge_store_probe=lambda _s: _ok("knowledge_store"),
        trace_store_probe=lambda _s: _ok("trace_store"),
    )

    result = service.check()

    assert result.status == STATUS_UNAVAILABLE
    assert result.http_status == 503
    assert result.checks["settings"].code == "settings_invalid"
    # Knowledge / trace probes skipped when settings reload fails.
    assert set(result.checks) == {"settings"}


def test_check_uses_injected_settings_probe_when_supplied(
    settings_path: Path,
) -> None:
    calls: list[int] = []

    def probe() -> ReadinessCheck:
        calls.append(1)
        return ReadinessCheck("settings", "ok", detail="ok")

    service = ReadinessService(
        settings_path=settings_path,
        settings_probe=probe,
        knowledge_store_probe=lambda _s: _ok("knowledge_store"),
        trace_store_probe=lambda _s: _ok("trace_store"),
    )

    result = service.check()

    assert calls == [1]
    assert result.status == STATUS_READY


# --- Wire contract --------------------------------------------------------


def test_wire_payload_omits_raw_exception_text_and_paths() -> None:
    result = ReadinessResult(
        status=STATUS_UNAVAILABLE,
        checks={
            "settings": ReadinessCheck(
                "settings",
                "settings_invalid",
                detail="boom: /etc/secret/cfg.yaml",
            ),
            "knowledge_store": _ok("knowledge_store"),
            "trace_store": _ok("trace_store"),
        },
    )

    payload = result.to_wire()

    assert payload == {
        "status": "unavailable",
        "checks": {
            "settings": "settings_invalid",
            "knowledge_store": "ok",
            "trace_store": "ok",
        },
    }
    # No path or exception text leaks through the wire surface.
    serialized = str(payload)
    assert "/etc/secret" not in serialized
    assert "boom" not in serialized
