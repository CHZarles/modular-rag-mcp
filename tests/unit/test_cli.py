"""Contract tests for the agent-oriented MCP CLI."""

from __future__ import annotations

import base64
from typing import Any

from mcp import types

from extension.cli import main as cli


def test_query_maps_arguments_headers_and_writes_structured_json(
    monkeypatch, capsys
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_tool(url, headers, tool_name, arguments):
        captured.update(
            url=url,
            headers=headers,
            tool_name=tool_name,
            arguments=arguments,
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ignored")],
            structuredContent={"results": [{"chunk_id": "chunk-1"}]},
        )

    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    exit_code = cli.main(
        [
            "query",
            "retrieval flow",
            "--collection",
            "docs",
            "--top-k",
            "3",
            "--url",
            "http://127.0.0.1:9000/mcp",
            "--request-id",
            "req-1",
            "--actor-key",
            "agent-1",
            "--session-id",
            "session-1",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"results":[{"chunk_id":"chunk-1"}]}\n'
    assert captured == {
        "url": "http://127.0.0.1:9000/mcp",
        "headers": {
            "X-Request-ID": "req-1",
            "X-RAG-Actor-Key": "agent-1",
            "X-RAG-Session-ID": "session-1",
        },
        "tool_name": "query_knowledge_hub",
        "arguments": {"query": "retrieval flow", "collection": "docs", "top_k": 3},
    }


def test_collections_uses_environment_endpoint(monkeypatch, capsys) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_tool(url, headers, tool_name, arguments):
        captured.update(url=url, headers=headers, tool_name=tool_name, arguments=arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ignored")],
            structuredContent={"collections": [], "total": 0},
        )

    monkeypatch.setenv("RAG_MCP_URL", "http://127.0.0.1:9001/mcp")
    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    assert cli.main(["collections"]) == 0
    assert capsys.readouterr().out == '{"collections":[],"total":0}\n'
    assert captured == {
        "url": "http://127.0.0.1:9001/mcp",
        "headers": {},
        "tool_name": "list_collections",
        "arguments": {},
    }


def test_tool_error_is_json_with_nonzero_exit(monkeypatch, capsys) -> None:
    async def fake_call_tool(*_args):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="not found")],
            structuredContent={"error": {"code": "document_not_found"}},
            isError=True,
        )

    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    assert cli.main(["document", "missing"]) == 1
    assert capsys.readouterr().out == '{"error":{"code":"document_not_found"}}\n'


def test_upload_reads_pdf_and_maps_ingestion_options(tmp_path, monkeypatch, capsys) -> None:
    captured: dict[str, Any] = {}
    pdf = tmp_path / "guide.pdf"
    content = b"%PDF-1.4\ndemo"
    pdf.write_bytes(content)

    async def fake_call_tool(url, headers, tool_name, arguments):
        captured.update(tool_name=tool_name, arguments=arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="queued")],
            structuredContent={"job": {"job_id": "job-1", "status": "queued"}},
        )

    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    assert cli.main(
        ["upload", str(pdf), "--collection", "docs", "--force", "--ai-enrichment"]
    ) == 0
    assert capsys.readouterr().out == '{"job":{"job_id":"job-1","status":"queued"}}\n'
    assert captured == {
        "tool_name": "upload_document",
        "arguments": {
            "filename": "guide.pdf",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "collection": "docs",
            "force": True,
            "ai_enrichment": True,
        },
    }


def test_upload_accepts_csv_without_parsing_it_locally(tmp_path, monkeypatch, capsys) -> None:
    captured: dict[str, Any] = {}
    source = tmp_path / "people.csv"
    content = b"name,role\nAlice,Architect\n"
    source.write_bytes(content)

    async def fake_call_tool(_url, _headers, tool_name, arguments):
        captured.update(tool_name=tool_name, arguments=arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="queued")],
            structuredContent={"job": {"job_id": "job-2", "status": "queued"}},
        )

    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    assert cli.main(["upload", str(source)]) == 0
    assert captured["tool_name"] == "upload_document"
    assert captured["arguments"]["filename"] == "people.csv"
    assert base64.b64decode(captured["arguments"]["content_base64"]) == content


def test_upload_rejects_unsupported_file_before_connecting(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not pdf", encoding="utf-8")

    async def unexpected_call(*_args):
        raise AssertionError("invalid files must not reach MCP")

    monkeypatch.setattr(cli, "_call_tool", unexpected_call)

    assert cli.main(["upload", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported file type" in captured.err


def test_job_maps_to_ingestion_job_tool(monkeypatch, capsys) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_tool(url, headers, tool_name, arguments):
        captured.update(tool_name=tool_name, arguments=arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="running")],
            structuredContent={"job": {"job_id": "job-1", "status": "running"}},
        )

    monkeypatch.setattr(cli, "_call_tool", fake_call_tool)

    assert cli.main(["job", "job-1"]) == 0
    assert capsys.readouterr().out == '{"job":{"job_id":"job-1","status":"running"}}\n'
    assert captured == {
        "tool_name": "get_ingestion_job",
        "arguments": {"job_id": "job-1"},
    }
