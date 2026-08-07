"""Hardened contract tests for the public MCP Tool surface (plan §C1/§5).

These tests cover the three acceptance criteria that are easy to regress:
* input boundary values (4000/20/128, +/-1, whitespace, Unicode)
* source-path redaction + citation metadata whitelist
* wire error envelopes must not leak secrets, stack traces, or absolute paths
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from mcp import types

from src.core.response import ResponseBuilder
from src.core.types import (
    CollectionInfo,
    DocumentSummary,
    JsonDict,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
)
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import (
    GetDocumentSummaryTool,
    QueryKnowledgeHubTool,
    ToolArgumentError,
    ToolExecutionError,
)
from src.mcp_server.tools.base import (
    MAX_IDENTIFIER_CHARS,
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    public_citation_metadata,
    public_source_label,
    sanitize_wire_string,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- Helpers ---------------------------------------------------------------

class _FakeService:
    def __init__(self, response: QueryResponse | None = None) -> None:
        self._response = response
        self.requests: list[QueryRequest] = []

    def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
        self.requests.append(request)
        assert self._response is not None, "service had no stubbed response"
        return self._response

    def list_collections(self) -> list[CollectionInfo]:  # pragma: no cover
        return []

    def get_document_summary(self, doc_id: str) -> DocumentSummary:  # pragma: no cover
        return DocumentSummary(doc_id=doc_id, source_path="stub.pdf")


def _citation_response(*, source_path: str, include_extra_metadata: bool = True) -> QueryResponse:
    metadata = {
        "source_path": source_path,
        "page": 3,
        "collection": "docs",
    }
    if include_extra_metadata:
        metadata.update(
            {
                "title": "RAG Manual",
                "tags": ["rag", "fence"],
                "rank": 1,
                "source": "fusion",
                "internal_claim": "GEN-2026-08",
            }
        )
    candidate = RetrievalCandidate(
        chunk_id="chunk-1",
        text="Generation fencing prevents stale publication.",
        metadata=metadata,
        score=0.91,
        source="fusion",
        rank=1,
    )
    return QueryResponse(
        results=[candidate],
        request_id="req-1",
    )


# --- Tools/list contract ---------------------------------------------------

def test_protocol_handler_registers_three_read_only_tools() -> None:
    handler = ProtocolHandler("test", "0.1.0")
    handler.register_tool(QueryKnowledgeHubTool(lambda: _FakeService()))
    handler.register_tool(_FakeListCollections())
    handler.register_tool(GetDocumentSummaryTool(lambda: _FakeService()))

    listed = handler.handle_tools_list()
    names = [tool.name for tool in listed.tools]
    assert names == ["query_knowledge_hub", "list_collections", "get_document_summary"]
    for tool in listed.tools:
        assert tool.input_schema.get("type") == "object"
        assert tool.input_schema.get("additionalProperties") is False


# --- Input boundary --------------------------------------------------------

class _FakeListCollections:
    name = "list_collections"
    description = "列出当前可用的知识集合及其文档、片段和图片数量"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def call(self, arguments: JsonDict) -> JsonDict:  # pragma: no cover - tested elsewhere
        return {"content": [], "structuredContent": {}}


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "\t\n"},
        {"query": 42},
        {"query": "x" * (MAX_QUERY_CHARS + 1)},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": MAX_TOP_K + 1},
        {"query": "x", "top_k": True},
        {"query": "x", "top_k": 1.5},
        {"query": "x", "top_k": "5"},
        {"query": "x", "collection": "   "},
        {"query": "x", "collection": "a" * (MAX_IDENTIFIER_CHARS + 1)},
        {"query": "x", "collection": 123},
        {"query": "x", "extra": "field"},
        {"query": "x", "top_k": 1, "collection": "c", "sneaky": True},
    ],
    ids=[
        "missing-query",
        "empty-query",
        "whitespace-only-query",
        "tab-newline-query",
        "int-query",
        "oversize-query",
        "top-k-zero",
        "top-k-over-max",
        "top-k-bool",
        "top-k-float",
        "top-k-string",
        "whitespace-only-collection",
        "oversize-collection",
        "int-collection",
        "unknown-field",
        "multiple-unknown-fields",
    ],
)
def test_query_knowledge_hub_rejects_invalid_inputs(arguments: JsonDict) -> None:
    tool = QueryKnowledgeHubTool(lambda: _FakeService(_citation_response(source_path="/knowledge/secret.pdf")))

    with pytest.raises(ToolArgumentError):
        tool.call(arguments)


@pytest.mark.parametrize(
    "query",
    ["x", "x" * MAX_QUERY_CHARS, "中" * MAX_QUERY_CHARS, "   hello   "],
)
def test_query_knowledge_hub_accepts_in_boundary_inputs(query: str) -> None:
    service = _FakeService(_citation_response(source_path="/knowledge/secret.pdf"))
    tool = QueryKnowledgeHubTool(lambda: service)

    result = tool.call({"query": query, "collection": "docs"})

    assert service.requests[0].query == query.strip()
    assert "secret.pdf" in result["content"][0]["text"]


@pytest.mark.parametrize(
    "doc_id",
    ["doc-1", "d" * MAX_IDENTIFIER_CHARS, "  spaced  "],
)
def test_get_document_summary_accepts_in_boundary_ids(doc_id: str) -> None:
    document = DocumentSummary(
        doc_id="doc-1",
        source_path="/knowledge/rag-guide.pdf",
        title="RAG Guide",
        summary="ok",
    )
    captured: dict[str, str] = {}

    class _Service:
        def get_document_summary(self, value: str) -> DocumentSummary:
            captured["id"] = value
            return document

        def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:  # pragma: no cover
            raise AssertionError

        def list_collections(self) -> list[CollectionInfo]:  # pragma: no cover
            raise AssertionError

    tool = GetDocumentSummaryTool(lambda: _Service())
    tool.call({"doc_id": doc_id})

    assert captured["id"] == doc_id.strip()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"doc_id": ""},
        {"doc_id": "   "},
        {"doc_id": 123},
        {"doc_id": "x" * (MAX_IDENTIFIER_CHARS + 1)},
        {"doc_id": "doc-1", "collection": "docs"},
        {"doc_id": "doc-1", "extra": True},
    ],
    ids=[
        "missing-doc-id",
        "empty-doc-id",
        "whitespace-only-doc-id",
        "int-doc-id",
        "oversize-doc-id",
        "unknown-collection",
        "unknown-extra",
    ],
)
def test_get_document_summary_rejects_invalid_inputs(arguments: JsonDict) -> None:
    tool = GetDocumentSummaryTool(lambda: _FakeService())

    with pytest.raises(ToolArgumentError):
        tool.call(arguments)


# --- Service-failure envelope ---------------------------------------------

def test_query_knowledge_hub_wraps_service_failure_in_tool_execution_error() -> None:
    class _BrokenService:
        def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:
            raise RuntimeError("boom")

        def list_collections(self) -> list[CollectionInfo]:
            return []

        def get_document_summary(self, doc_id: str) -> DocumentSummary:
            return DocumentSummary(doc_id=doc_id, source_path="stub.pdf")

    tool = QueryKnowledgeHubTool(lambda: _BrokenService())

    with pytest.raises(ToolExecutionError) as info:
        tool.call({"query": "x"})

    assert info.value.component_code == "query_failed"


# --- Source redaction + metadata whitelist --------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "/Users/private/secret.pdf",
        "/Users/private/secret.pdf",
        "\\Users\\private\\secret.pdf",
        "/var/data/2026/Q1/internal.pdf",
        "/tmp/../etc/passwd.pdf",
        "C:\\Users\\someone\\confidential.pdf",
        "/mnt/data/some/repo/source.PDF",
        "//host/share/docs/2026-01-15.pdf",
        "/data/files/finance-report.pdf",
        "  /knowledge/man.pdf ",
    ],
)
def test_public_source_label_drops_absolute_paths(raw: str) -> None:
    label = public_source_label(raw)
    assert "/" not in label
    assert "\\" not in label
    assert ".." not in label
    assert label.endswith(".pdf") or label.endswith(".PDF")


def test_response_builder_redacts_absolute_source_in_citation() -> None:
    response = _citation_response(source_path="/Users/private/secret.pdf")

    result = ResponseBuilder().build_mcp_result(response)
    citation = result["structuredContent"]["results"][0]

    assert citation["source"] == "secret.pdf"
    assert "Users" not in citation["source"]
    assert "/Users/" not in result["content"][0]["text"]
    assert "/private" not in result["content"][0]["text"]


def test_response_builder_filters_citation_metadata_to_public_whitelist() -> None:
    response = _citation_response(source_path="/knowledge/rag.pdf", include_extra_metadata=True)

    result = ResponseBuilder().build_mcp_result(response)
    metadata = result["structuredContent"]["results"][0]["metadata"]

    assert set(metadata) <= {"collection", "section", "title", "tags"}
    assert "internal_claim" not in metadata
    assert "rank" not in metadata
    assert metadata.get("collection") == "docs"


def test_get_document_summary_redacts_source_path_and_filters_metadata() -> None:
    document = DocumentSummary(
        doc_id="doc-1",
        source_path="/knowledge/secret.pdf",
        title="RAG Guide",
        summary="ok",
        tags=["rag"],
        metadata={
            "collection": "docs",
            "chunk_count": 12,
            "secret_internal": "leaky",
        },
    )

    result = GetDocumentSummaryTool(lambda: _FakeService()).call.__wrapped__ if False else _stub_call(document)

    payload = result["structuredContent"]["document"]
    assert payload["source_path"] == "secret.pdf"
    assert "secret_internal" not in payload["metadata"]
    assert payload["metadata"] == {"collection": "docs"}


def _stub_call(document: DocumentSummary) -> JsonDict:
    class _Service:
        def get_document_summary(self, doc_id: str) -> DocumentSummary:
            return document

        def query(self, request: QueryRequest, trace: object | None = None) -> QueryResponse:  # pragma: no cover
            raise AssertionError

        def list_collections(self) -> list[CollectionInfo]:  # pragma: no cover
            raise AssertionError

    return GetDocumentSummaryTool(lambda: _Service()).call({"doc_id": document.doc_id})


def test_public_citation_metadata_is_idempotent_on_empty() -> None:
    assert public_citation_metadata(None) == {}
    assert public_citation_metadata({}) == {}


# --- Wire error envelope ---------------------------------------------------

_LEAK_PATTERNS = (
    "secret",
    "/Users/",
    "/private",
    "Traceback",
    "File \"",
    "Object ",
)


@pytest.mark.parametrize(
    "scenario",
    [
        "tool-not-found",
        "invalid-arguments",
        "service-execution-failure",
        "invalid-mcp-result",
    ],
)
def test_protocol_handler_envelope_redacts_internals(scenario: str) -> None:
    os.environ["PROJECT_SECRET_KEY"] = "leak-me"
    handler = ProtocolHandler("test", "0.1.0")

    class _BadHandlerTool:
        name = "torture"
        description = "always raises"
        input_schema: JsonDict = {"type": "object", "properties": {}, "additionalProperties": False}

        def call(self, arguments: JsonDict) -> JsonDict:
            if scenario == "invalid-arguments":
                raise ToolArgumentError("bad input from /Users/private/secret.pdf ${PROJECT_SECRET_KEY}")
            if scenario == "service-execution-failure":
                raise ToolExecutionError("query_failed")
            if scenario == "invalid-mcp-result":
                return {"definitely": "not a CallToolResult"}
            raise RuntimeError("uncaught /Users/private/secret.pdf boom")

    handler.register_tool(_BadHandlerTool())

    async def _invoke() -> types.ErrorData:
        if scenario == "tool-not-found":
            return await handler.handle_tools_call("missing", {})  # type: ignore[return-value]
        return await handler.handle_tools_call("torture", {})  # type: ignore[return-value]

    result = asyncio.run(_invoke())
    assert isinstance(result, types.ErrorData)

    assert isinstance(result, types.ErrorData)

    serialized = json.dumps(result.model_dump(mode="json"))
    assert "leak-me" not in serialized
    assert "Traceback" not in serialized
    assert "/Users/" not in serialized

    data = result.data or {}
    assert data.get("name") in {"missing", "torture"}
    assert "component_code" in data
    assert "reason" not in data  # never leak original exception text


# --- sanitize_wire_string --------------------------------------------------

def test_sanitize_wire_string_redacts_absolute_paths_and_env_refs() -> None:
    os.environ["LEAK_TOKEN"] = "leak-value"
    text = (
        "found /etc/passwd.pdf and ${LEAK_TOKEN} in ${MISSING_ENV}"
    )

    scrubbed = sanitize_wire_string(text)

    assert "/etc/passwd.pdf" not in scrubbed
    assert "leak-value" not in scrubbed
    assert "${MISSING_ENV}" in scrubbed  # unresolved env refs are not secrets
    assert "leak" not in scrubbed.lower().split("token")[0]
