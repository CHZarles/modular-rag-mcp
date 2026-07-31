"""MCP Server 的 Stdio 生命周期和依赖注入集成测试。"""

from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp import types
from mcp_types.version import LATEST_HANDSHAKE_VERSION

from src.core.types import CollectionInfo, DocumentSummary, QueryRequest, QueryResponse
from src.mcp_server.server import SERVER_NAME, SERVER_VERSION, create_mcp_server

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeKnowledgeService:
    def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
        return QueryResponse(answer="fake", citations=[], items=[])

    def list_collections(self) -> list[CollectionInfo]:
        return []

    def get_document_summary(self, doc_id: str) -> DocumentSummary:
        return DocumentSummary(doc_id=doc_id, source_path="fake.pdf")


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
    assert responses[2]["result"]["tools"] == []
    assert responses[3]["error"]["code"] == types.METHOD_NOT_FOUND
    assert responses[4]["error"]["code"] == types.METHOD_NOT_FOUND
    assert responses[5]["error"]["code"] == types.INVALID_PARAMS
