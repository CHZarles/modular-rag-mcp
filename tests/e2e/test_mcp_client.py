"""Official MCP client acceptance test over a real stdio subprocess."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pymupdf
import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

from scripts.ingest import main as ingest_main
from src.libs.embedding import EmbeddingFactory

pytestmark = pytest.mark.e2e
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MCPClientEmbedding:
    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        return [[float(len(text)), float(sum(text.encode("utf-8")) % 997 + 1)] for text in texts]


def test_official_client_queries_real_index_over_stdio(tmp_path: Path) -> None:
    document_path = tmp_path / "documents" / "generation-lifecycle.pdf"
    _write_pdf(
        document_path,
        "A generation fence prevents stale ingestion workers from publishing old results.",
    )
    settings_path = _write_settings(tmp_path)
    EmbeddingFactory.register("mcp-client-deterministic", lambda config: MCPClientEmbedding())
    try:
        assert (
            ingest_main(
                ["--path", str(document_path), "--collection", "docs"],
                settings_path=settings_path,
            )
            == 0
        )
    finally:
        EmbeddingFactory.unregister("mcp-client-deterministic")

    result = anyio.run(_query_over_stdio, settings_path)

    assert result.is_error is False
    assert isinstance(result.content[0], types.TextContent)
    assert result.structured_content is not None
    citations = result.structured_content["citations"]
    assert citations
    assert citations[0]["source"] == str(document_path.resolve())
    assert "generation fence" in citations[0]["text"].lower()


async def _query_over_stdio(settings_path: Path) -> types.CallToolResult:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_server.server"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "RAG_SETTINGS_PATH": str(settings_path)},
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "modular-rag-mcp"

            listed = await session.list_tools()
            assert "query_knowledge_hub" in {tool.name for tool in listed.tools}

            return await session.call_tool(
                "query_knowledge_hub",
                {"query": "generation fence", "top_k": 2, "collection": "docs"},
            )


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _write_settings(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    content = f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
embedding:
  provider: mcp-client-deterministic
splitter:
  provider: recursive
  chunk_size: 300
  chunk_overlap: 30
ingestion:
  claim_lease_seconds: 60
  batch_size: 2
  storage:
    integrity_db_path: {data / "db/integrity.db"}
    bm25_path: {data / "db/bm25"}
    image_db_path: {data / "db/images.db"}
    image_root: {data / "images"}
  chunk_refiner:
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    enabled: false
vector_store:
  backend: chroma
  persist_path: {data / "db/chroma"}
  collection_name: mcp-client
  distance_metric: cosine
retrieval:
  enable_dense: false
  enable_sparse: true
  sparse_backend: bm25
  fusion_algorithm: rrf
  top_k_dense: 5
  top_k_sparse: 5
  top_k_final: 3
rerank:
  backend: none
  top_m: 5
  timeout_seconds: 5
evaluation:
  backends: [custom]
observability:
  enabled: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path
