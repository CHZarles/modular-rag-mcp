"""MCP Server 的 Stdio 生命周期和依赖注入集成测试。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

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
    assert "Starting modular-rag-mcp" in completed.stderr
