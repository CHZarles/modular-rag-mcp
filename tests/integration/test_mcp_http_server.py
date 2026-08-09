"""Integration tests for the MCP Streamable HTTP server (plan §C2.1).

Verifies the per-request identity contract end-to-end:

* upstream ``X-Request-ID`` / ``X-RAG-Actor-Key`` / ``X-RAG-Session-ID`` headers
  arrive intact in the Tool worker thread and are accessible through
  ``get_request_context()``;
* missing ``X-Request-ID`` triggers server-side UUID generation that
  differs across calls;
* invalid Header values are rejected with MCP ``INVALID_PARAMS``;
* concurrent HTTP calls each retain their own actor/session/request_id.

The server runs in-process via ``uvicorn.Server.serve`` in a daemon thread
because the MCP Streamable HTTP transport only enters its session manager
inside the ASGI lifespan — which ASGITransport does not trigger on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    QueryRequest,
    QueryResponse,
)
from src.mcp_server import request_context as request_context_module
from src.mcp_server.server import create_mcp_server

# --- Test fakes ------------------------------------------------------------

@dataclass
class CapturedContext:
    request_id: str | None
    actor_key: str | None
    session_id: str | None


class _ContextCapturingService:
    """Fake KnowledgeService that records the active ``RequestContext``."""

    def __init__(self) -> None:
        self.captured: list[CapturedContext] = []
        self.requests: list[QueryRequest] = []
        self._lock = threading.Lock()

    def query(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> QueryResponse:
        with self._lock:
            self.requests.append(request)
        ctx = request_context_module.get_request_context()
        with self._lock:
            self.captured.append(
                CapturedContext(
                    request_id=ctx.request_id,
                    actor_key=ctx.actor_key,
                    session_id=ctx.session_id,
                )
            )
        return QueryResponse(
            results=[],
            request_id=ctx.request_id,
        )

    def list_collections(self) -> list[CollectionInfo]:
        return []

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        return DocumentSummary(doc_id=doc_id, source_path="stub.pdf")


# --- Uvicorn fixture -------------------------------------------------------

class _UvicornThread:
    def __init__(self, app: Any, host: str, port: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            loop="asyncio",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.config.port)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="mcp-http-test-server"
        )
        self._thread.start()
        # Wait until the server is actually accepting connections.
        for _ in range(100):
            if self._server.started:
                return
            threading.Event().wait(0.05)
        raise RuntimeError("uvicorn did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def mcp_server() -> Iterator[tuple[str, _ContextCapturingService]]:
    service = _ContextCapturingService()
    server = create_mcp_server(service)
    app = server.streamable_http_app(streamable_http_path="/mcp")
    port = _free_port()
    runner = _UvicornThread(app, host="127.0.0.1", port=port)
    runner.start()
    try:
        yield f"http://127.0.0.1:{runner.port}/mcp", service
    finally:
        runner.stop()


# --- Helpers ---------------------------------------------------------------

@contextlib.asynccontextmanager
async def _http_client(
    base_url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    async with streamable_http_client(
        base_url,
        http_client=httpx.AsyncClient(
            headers=dict(headers or {}),
            timeout=httpx.Timeout(10.0, read=10.0),
            trust_env=False,
        ),
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call_query(
    session: ClientSession,
    *,
    arguments: Mapping[str, Any],
) -> Any:
    """Call query_knowledge_hub and surface any JSON-RPC error as a tuple.

    The MCP Streamable HTTP transport converts handler-side ``ErrorData`` into
    a JSON-RPC error envelope which the client re-raises as :class:`MCPError`.
    ``CallToolResult`` is what happy-path calls yield. Returning one of those
    two shapes keeps tests short.
    """
    from mcp.shared.exceptions import MCPError

    try:
        return await session.call_tool("query_knowledge_hub", dict(arguments))
    except MCPError as exc:
        return exc


# --- Header propagation ----------------------------------------------------

@pytest.mark.anyio
async def test_http_header_actor_and_session_propagate_to_tool_worker(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    base_url, service = mcp_server
    headers = {
        "X-Request-ID": "req-explicit-1",
        "X-RAG-Actor-Key": "actor-supplier-9",
        "X-RAG-Session-ID": "session-supplier-9",
    }
    async with _http_client(base_url, headers=headers) as session:
        result = await _call_query(session, arguments={"query": "hello"})

    assert result.is_error is False
    captured = service.captured[-1]
    assert captured.request_id == "req-explicit-1"
    assert captured.actor_key == "actor-supplier-9"
    assert captured.session_id == "session-supplier-9"


@pytest.mark.anyio
async def test_http_request_id_missing_generates_unique_uuid_per_call(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    base_url, service = mcp_server
    async with _http_client(base_url) as session:
        await _call_query(session, arguments={"query": "first"})
        await _call_query(session, arguments={"query": "second"})

    seen_ids = [capture.request_id for capture in service.captured]
    assert len(seen_ids) == 2
    assert all(seen_id and len(seen_id) >= 16 for seen_id in seen_ids)
    assert seen_ids[0] != seen_ids[1]


@pytest.mark.anyio
async def test_http_actor_and_session_default_to_none_when_unset(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    base_url, service = mcp_server
    async with _http_client(base_url) as session:
        await _call_query(session, arguments={"query": "anon"})

    captured = service.captured[-1]
    assert captured.request_id and len(captured.request_id) >= 16
    assert captured.actor_key is None
    assert captured.session_id is None


@pytest.mark.anyio
async def test_http_oversize_actor_header_returns_invalid_params(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    from mcp.shared.exceptions import MCPError

    base_url, _ = mcp_server
    headers = {"X-RAG-Actor-Key": "a" * 200}
    async with _http_client(base_url, headers=headers) as session:
        outcome = await _call_query(session, arguments={"query": "x"})

    assert isinstance(outcome, MCPError)
    assert outcome.code == types.INVALID_PARAMS
    assert "header" in (outcome.message or "").lower()


# --- Concurrency isolation ------------------------------------------------

@pytest.mark.anyio
async def test_http_concurrent_calls_generate_distinct_request_ids(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    base_url, service = mcp_server
    n = 30

    async with _http_client(base_url) as session:
        await asyncio.gather(
            *[_call_query(session, arguments={"query": f"q{i}"}) for i in range(n)]
        )

    captured = service.captured
    assert len(captured) == n
    request_ids = [item.request_id for item in captured]
    # UUID generation produced N unique ids.
    assert len(set(request_ids)) == n


@pytest.mark.anyio
async def test_http_concurrent_clients_with_distinct_headers_isolate_contexts(
    mcp_server: tuple[str, _ContextCapturingService],
) -> None:
    """Plan §C2.1 / FR-04: concurrent HTTP callers each retain their own
    actor/session/request_id.

    The MCP Streamable HTTP transport does not propagate the JSON-RPC
    ``_meta`` block as HTTP headers — every call on a given ``ClientSession``
    shares the underlying ``httpx.AsyncClient``. The realistic production
    path is one httpx client per upstream caller, each configured with its
    own ``X-RAG-*`` headers. Twenty such clients call in parallel; the
    recorded contexts must be perfectly segregated.
    """
    base_url, service = mcp_server
    n = 20

    async def _one(index: int) -> None:
        headers = {
            "X-Request-ID": f"rid-{index}",
            "X-RAG-Actor-Key": f"actor-{index}",
            "X-RAG-Session-ID": f"session-{index}",
        }
        async with _http_client(base_url, headers=headers) as session:
            await _call_query(session, arguments={"query": f"q{index}"})

    await asyncio.gather(*[_one(i) for i in range(n)])

    captured = service.captured
    assert len(captured) == n
    seen_actors = {entry.actor_key for entry in captured}
    expected_actors = {f"actor-{i}" for i in range(n)}
    assert seen_actors == expected_actors
    seen_sessions = {entry.session_id for entry in captured}
    assert seen_sessions == {f"session-{i}" for i in range(n)}
    request_ids = [entry.request_id for entry in captured]
    assert len(set(request_ids)) == n



def _write_settings_yaml(tmp_path: Path, observability_enabled: str, trace_db: Path | None) -> Path:
    settings_path = tmp_path / "settings.yaml"
    trace_line = (
        f"  trace_db_path: {trace_db}\n" if trace_db is not None else ""
    )
    settings_path.write_text(
        "knowledge_service:\n  mode: local\n"
        "llm:\n  provider: openai\n"
        "embedding:\n  provider: hash\n  dimension: 8\n"
        "splitter:\n  provider: recursive\n  chunk_size: 32\n  chunk_overlap: 4\n"
        f"vector_store:\n  backend: chroma\n  persist_path: {tmp_path}/vector\n"
        "retrieval:\n  sparse_backend: bm25\n  top_k_dense: 5\n  top_k_sparse: 5\n  top_k_final: 3\n"
        "rerank:\n  backend: none\n  top_m: 5\n  timeout_seconds: 5\n"
        "evaluation:\n  backends: [custom]\n"
        f"observability:\n  enabled: {observability_enabled}\n{trace_line}",
        encoding="utf-8",
    )
    return settings_path


# --- Health endpoints (plan §5.7 / §C4) -----------------------------------


@pytest.fixture
def mcp_http_app(tmp_path: Path) -> Iterator[tuple[str, _ContextCapturingService, Path]]:
    """Build the full HTTP app (MCP + /health/*) and expose its base URL."""
    from src.mcp_server.http_server import build_app

    service = _ContextCapturingService()
    settings_path = _write_settings_yaml(tmp_path, "false", None)
    app = build_app(knowledge_service=service, settings_path=settings_path)
    port = _free_port()
    runner = _UvicornThread(app, host="127.0.0.1", port=port)
    runner.start()
    try:
        yield f"http://127.0.0.1:{runner.port}", service, settings_path
    finally:
        runner.stop()


@pytest.mark.anyio
async def test_health_live_returns_ok_without_building_knowledge_service(
    mcp_http_app: tuple[str, _ContextCapturingService, Path],
) -> None:
    base_url, service, _settings_path = mcp_http_app

    async with httpx.AsyncClient(base_url=base_url, timeout=5.0, trust_env=False) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # ``live`` must not call into the KnowledgeService; the fake would have
    # raised if any Tool was invoked. (No assertion on captures — only Tools
    # touch the service, and live never reaches them.)
    assert service.captured == []


@pytest.mark.anyio
async def test_health_ready_reports_all_ok(mcp_http_app: tuple[str, _ContextCapturingService, Path]) -> None:
    base_url, _service, _settings = mcp_http_app

    async with httpx.AsyncClient(base_url=base_url, timeout=5.0, trust_env=False) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "settings": "ok",
        "knowledge_store": "ok",
        "trace_store": "ok",
    }


@pytest.mark.anyio
async def test_health_ready_reports_503_when_settings_invalid(
    tmp_path: Path,
) -> None:
    from src.mcp_server.http_server import build_app

    service = _ContextCapturingService()
    missing_settings = tmp_path / "missing.yaml"
    app = build_app(knowledge_service=service, settings_path=missing_settings)
    port = _free_port()
    runner = _UvicornThread(app, host="127.0.0.1", port=port)
    runner.start()
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=5.0, trust_env=False
        ) as client:
            response = await client.get("/health/ready")
    finally:
        runner.stop()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"settings": "settings_invalid"}
    # No path / exception text leaks onto the wire.
    serialized = response.text
    assert str(missing_settings) not in serialized
    assert "FileNotFoundError" not in serialized


@pytest.mark.anyio
async def test_health_ready_reports_degraded_when_trace_store_unwritable(
    tmp_path: Path,
) -> None:
    from src.core.trace import SQLiteTraceStore
    from src.mcp_server.http_server import build_app

    service = _ContextCapturingService()
    settings_path = _write_settings_yaml(tmp_path, "true", tmp_path / "traces.db")

    # Build a writable store, then chmod the DB file so check_writable fails.
    db_path = tmp_path / "traces.db"
    SQLiteTraceStore(db_path, auto_purge=False)
    db_path.chmod(0o400)
    try:
        store = SQLiteTraceStore(db_path, auto_purge=False)
    except Exception:
        # The store may fail to bootstrap on a read-only file; in that case
        # the probe still has to mark trace_store as unwritable.
        store = None

    try:
        app = build_app(
            knowledge_service=service,
            trace_store=store,
            settings_path=settings_path,
        )
        port = _free_port()
        runner = _UvicornThread(app, host="127.0.0.1", port=port)
        runner.start()
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=5.0, trust_env=False
            ) as client:
                response = await client.get("/health/ready")
        finally:
            runner.stop()
    finally:
        db_path.chmod(0o644)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["trace_store"] == "unwritable"
    assert body["checks"]["settings"] == "ok"
    assert body["checks"]["knowledge_store"] == "ok"


@pytest.mark.anyio
async def test_health_endpoints_do_not_invoke_llm_or_embedding(
    mcp_http_app: tuple[str, _ContextCapturingService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ready`` must NOT touch LLM/Embedding — mock proof per plan §C4."""
    from src.libs.embedding import embedding_factory
    from src.libs.llm import llm_factory

    base_url, _service, _settings = mcp_http_app

    def _fail(_settings: object) -> object:
        raise AssertionError("LLM/Embedding factory must not run during readiness")

    monkeypatch.setattr(llm_factory, "build_llm", _fail, raising=False)
    monkeypatch.setattr(embedding_factory, "build_embedding", _fail, raising=False)

    async with httpx.AsyncClient(base_url=base_url, timeout=5.0, trust_env=False) as client:
        response = await client.get("/health/ready")

    # The readiness probe completes without invoking any provider factory.
    assert response.status_code == 200
