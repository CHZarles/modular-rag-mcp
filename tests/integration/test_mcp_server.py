"""MCP Server 的 Stdio 生命周期和依赖注入集成测试。"""

from __future__ import annotations

import asyncio
import base64
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
from mcp import types
from mcp.client.session import ClientSession
from mcp_types.version import LATEST_HANDSHAKE_VERSION

from src.core.response import ResponseBuilder
from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    ImagePayload,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.mcp_server.server import SERVER_NAME, SERVER_VERSION, create_mcp_server

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeKnowledgeService:
    def __init__(self) -> None:
        self.requests: list[QueryRequest] = []

    def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
        self.requests.append(request)
        return QueryResponse(answer="fake", citations=[], items=[])

    def list_collections(self) -> list[CollectionInfo]:
        return []

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        return DocumentSummary(doc_id=doc_id, source_path="fake.pdf")


def _call_tool(
    service: FakeKnowledgeService,
    name: str,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    async def scenario() -> types.CallToolResult:
        server = create_mcp_server(service)
        client_send, server_receive = anyio.create_memory_object_stream[Any](10)
        server_send, client_receive = anyio.create_memory_object_stream[Any](10)
        result: types.CallToolResult | None = None

        async with client_send, server_receive, server_send, client_receive:
            async with anyio.create_task_group() as task_group:
                async def run_server() -> None:
                    await server.run(
                        server_receive,
                        server_send,
                        server.create_initialization_options(),
                    )

                task_group.start_soon(run_server)
                async with ClientSession(client_receive, client_send) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert [tool.name for tool in listed.tools] == [
                        "query_knowledge_hub",
                        "list_collections",
                        "get_document_summary",
                    ]
                    result = await session.call_tool(name, arguments)
                task_group.cancel_scope.cancel()
        if result is None:
            raise AssertionError(f"{name} did not return a result")
        return result

    return anyio.run(scenario)


def _exchange_messages(
    messages: list[dict[str, Any]],
    *,
    expected_responses: int,
    timeout: float = 10,
) -> list[dict[str, Any]]:
    """保持 stdin 打开，等待异步 Tool 请求全部返回后再停止子进程。"""
    process = subprocess.Popen(
        [sys.executable, "-m", "src.mcp_server.server"],
        cwd=PROJECT_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = process.stdout

    lines: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        for line in stdout:
            lines.put(line)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

        responses: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while len(responses) < expected_responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"MCP response timeout: {responses}")
            try:
                raw = lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise AssertionError(f"MCP response timeout: {responses}") from exc
            message = json.loads(raw)
            if "id" in message:
                responses.append(message)
        return responses
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=1)


def test_server_lifespan_exposes_injected_knowledge_service() -> None:
    service = FakeKnowledgeService()
    server = create_mcp_server(service)

    async def read_context() -> object:
        async with server.lifespan(server) as context:
            return context.knowledge_service

    assert asyncio.run(read_context()) is service


def test_stdio_subprocess_initializes_without_polluting_stdout() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_HANDSHAKE_VERSION,
            "clientInfo": {"name": "pytest", "version": "1.0"},
            "capabilities": {},
        },
    }

    completed = subprocess.run(
        [sys.executable, "-m", "src.mcp_server.server"],
        input=json.dumps(request) + "\n",
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, "initialize 没有返回 JSON-RPC 响应"
    messages = [json.loads(line) for line in lines]
    assert all(message.get("jsonrpc") == "2.0" for message in messages)

    response = next(message for message in messages if message.get("id") == 1)
    assert response["result"]["protocolVersion"] == LATEST_HANDSHAKE_VERSION
    assert response["result"]["serverInfo"] == {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
    }
    assert "capabilities" in response["result"]
    assert "tools" in response["result"]["capabilities"]
    assert "Starting modular-rag-mcp" in completed.stderr


def test_stdio_routes_tools_list_and_returns_standard_protocol_errors() -> None:
    messages: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_HANDSHAKE_VERSION,
                "clientInfo": {"name": "pytest", "version": "1.0"},
                "capabilities": {},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}},
    ]

    received = _exchange_messages(messages, expected_responses=5)
    responses = {
        message["id"]: message
        for message in received
    }
    assert [tool["name"] for tool in responses[2]["result"]["tools"]] == [
        "query_knowledge_hub",
        "list_collections",
        "get_document_summary",
    ]
    assert responses[3]["error"]["code"] == types.METHOD_NOT_FOUND
    assert responses[4]["error"]["code"] == types.METHOD_NOT_FOUND
    assert responses[5]["error"]["code"] == types.INVALID_PARAMS


def test_query_knowledge_hub_runs_through_official_mcp_session() -> None:
    service = FakeKnowledgeService()
    result = _call_tool(
        service,
        "query_knowledge_hub",
        {"query": "generation fence", "top_k": 2, "collection": "docs"},
    )

    assert result.is_error is False
    assert isinstance(result.content[0], types.TextContent)
    assert result.content[0].text == "未找到相关知识库内容。"
    structured = dict(result.structured_content)
    # trace_id is a fresh UUID per request; assert shape + presence, not value.
    trace_id = structured.pop("trace_id")
    assert isinstance(trace_id, str) and trace_id
    assert structured == {
        "answer": "未找到相关知识库内容。",
        "citations": [],
        "request_id": None,
        "metadata": {},
    }
    assert service.requests == [
        QueryRequest(query="generation fence", top_k=2, collection="docs")
    ]


def test_local_query_response_reads_and_deduplicates_chunk_images(tmp_path: Path) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nlocal-image"
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(image_bytes)
    image = {
        "image_id": "diagram-1",
        "path": str(image_path),
        "mime_type": "image/png",
    }
    missing_image = {
        "image_id": "missing",
        "path": str(tmp_path / "missing.png"),
        "mime_type": "image/png",
    }
    candidates = [
        RetrievalCandidate(
            chunk_id=f"chunk-{index}",
            text="The architecture diagram explains the retrieval flow.",
            metadata={"source_path": "manual.pdf", "images": [image, missing_image]},
            score=0.9 - index / 10,
            source="rerank",
            rank=index,
        )
        for index in (1, 2)
    ]
    builder = ResponseBuilder()

    response = builder.build(
        QueryRequest(query="retrieval flow", include_images=True),
        candidates,
    )
    result = builder.build_mcp_result(response)

    assert len(response.images) == 1
    assert response.images[0].data_base64 == base64.b64encode(image_bytes).decode("ascii")
    assert result["content"][1] == {
        "type": "image",
        "data": response.images[0].data_base64,
        "mimeType": "image/png",
    }
    assert base64.b64decode(result["content"][1]["data"], validate=True) == image_bytes


def test_mcp_server_uses_remote_base64_without_reading_remote_image_path() -> None:
    image_bytes = b"\xff\xd8\xffremote-image"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    candidate = RetrievalCandidate(
        chunk_id="chunk-remote",
        text="Remote retrieval result with an image.",
        metadata={"source_path": "remote.pdf"},
        score=0.9,
        source="rerank",
        rank=1,
    )

    class RemoteKnowledgeService(FakeKnowledgeService):
        def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
            self.requests.append(request)
            return QueryResponse(
                answer="Remote answer.",
                citations=[],
                items=[candidate],
                images=[
                    ImagePayload(
                        image_id="remote-1",
                        mime_type="image/jpeg",
                        data_base64=encoded,
                        uri="/remote-host/images/remote-1.jpg",
                    )
                ],
            )

    service = RemoteKnowledgeService()
    with patch.object(
        Path,
        "read_bytes",
        side_effect=AssertionError("remote image paths must not be read"),
    ):
        result = _call_tool(
            service,
            "query_knowledge_hub",
            {"query": "remote image", "collection": "docs"},
        )

    assert result.is_error is False
    assert isinstance(result.content[1], types.ImageContent)
    assert result.content[1].mime_type == "image/jpeg"
    assert base64.b64decode(result.content[1].data, validate=True) == image_bytes


def test_query_knowledge_hub_response_trace_id_correlates_with_sqlite_row(
    tmp_path: Path,
) -> None:
    """End-to-end: trace_id in structuredContent matches the persisted row."""
    from src.core.trace import SQLiteTraceStore
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.tools import QueryKnowledgeHubTool

    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    service = FakeKnowledgeService()

    # Build a server with a custom protocol handler that wires the SQLite
    # store directly — bypasses the production settings-based factory.
    handler = ProtocolHandler(SERVER_NAME, SERVER_VERSION)
    from src.mcp_server.tools import (
        GetDocumentSummaryTool,
        ListCollectionsTool,
    )
    handler.register_tool(
        QueryKnowledgeHubTool(
            get_service=lambda: service,
            get_collector=lambda: store,
        )
    )
    handler.register_tool(ListCollectionsTool(lambda: service))
    handler.register_tool(GetDocumentSummaryTool(lambda: service))

    async def scenario() -> Any:
        server = create_mcp_server(service, protocol_handler=handler)
        client_send, server_receive = anyio.create_memory_object_stream[Any](10)
        server_send, client_receive = anyio.create_memory_object_stream[Any](10)
        result: types.CallToolResult | None = None
        async with client_send, server_receive, server_send, client_receive:
            async with anyio.create_task_group() as task_group:
                async def run_server() -> None:
                    await server.run(
                        server_receive,
                        server_send,
                        server.create_initialization_options(),
                    )

                task_group.start_soon(run_server)
                async with ClientSession(client_receive, client_send) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "query_knowledge_hub",
                        {"query": "lease", "collection": "docs"},
                    )
                task_group.cancel_scope.cancel()
        if result is None:
            raise AssertionError("tool did not return a result")
        return result

    final = anyio.run(scenario)

    trace_id = final.structured_content["trace_id"]  # type: ignore[attr-defined]
    assert isinstance(trace_id, str) and trace_id

    rows = store.list_recent("query", limit=1)
    assert len(rows) == 1
    assert rows[0]["trace_id"] == trace_id
    assert rows[0]["metadata"]["status"] == "success"
    assert rows[0]["metadata"]["result_count"] == 0


def test_mcp_response_skips_uri_only_and_invalid_base64_images() -> None:
    response = QueryResponse(
        answer="Text remains available.",
        citations=[],
        items=[
            RetrievalCandidate(
                chunk_id="chunk-1",
                text="Text remains available.",
                metadata={},
                score=0.8,
                source="rerank",
                rank=1,
            )
        ],
        images=[
            ImagePayload(
                image_id="uri-only",
                mime_type="image/png",
                uri="/remote-host/images/uri-only.png",
            ),
            ImagePayload(
                image_id="invalid",
                mime_type="image/png",
                data_base64="not base64!",
                uri="/remote-host/images/invalid.png",
            ),
        ],
    )

    result = ResponseBuilder().build_mcp_result(response)

    assert result["content"] == [{"type": "text", "text": "Text remains available."}]
