"""JSON-only command-line client for the local Streamable HTTP MCP service."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from src.libs.loader.format_router import SUPPORTED_EXTENSIONS

DEFAULT_MCP_URL = "http://127.0.0.1:8766/mcp"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class _CliInputError(ValueError):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run one MCP Tool call and write its structured result as JSON."""
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except _CliInputError as exc:
        print(f"modular-rag-cli: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"modular-rag-cli: MCP request failed: {exc}", file=sys.stderr)
        return 2

    json.dump(result.structured_content, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 1 if result.is_error else 0


async def _run(args: argparse.Namespace) -> types.CallToolResult:
    tool_name, arguments = _tool_call(args)
    return await _call_tool(args.url, _request_headers(args), tool_name, arguments)


async def _call_tool(
    url: str,
    headers: Mapping[str, str],
    tool_name: str,
    arguments: Mapping[str, Any],
) -> types.CallToolResult:
    async with httpx2.AsyncClient(
        headers=dict(headers),
        timeout=httpx2.Timeout(10.0, read=60.0),
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, dict(arguments))


def _tool_call(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.command == "collections":
        return "list_collections", {}
    if args.command == "query":
        return "query_knowledge_hub", {
            "query": args.query,
            "collection": args.collection,
            "top_k": args.top_k,
        }
    if args.command == "document":
        return "get_document_summary", {"doc_id": args.doc_id}
    if args.command == "upload":
        content = _read_upload(args.path)
        return "upload_document", {
            "filename": args.path.name,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "collection": args.collection,
            "force": args.force,
            "ai_enrichment": args.ai_enrichment,
        }
    if args.command == "job":
        return "get_ingestion_job", {"job_id": args.job_id}
    raise ValueError(f"unsupported command: {args.command}")


def _request_headers(args: argparse.Namespace) -> dict[str, str]:
    values = {
        "X-Request-ID": args.request_id,
        "X-RAG-Actor-Key": args.actor_key,
        "X-RAG-Session-ID": args.session_id,
    }
    return {name: value for name, value in values.items() if value is not None}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modular-rag-cli",
        description="Call one Tool on a Streamable HTTP Modular RAG MCP server.",
    )
    _add_connection_options(parser, defaults=True)
    commands = parser.add_subparsers(dest="command", required=True)

    collections = commands.add_parser("collections", help="List available collections")
    _add_connection_options(collections, defaults=False)

    query = commands.add_parser("query", help="Query a collection")
    query.add_argument("query")
    query.add_argument("--collection", default="default")
    query.add_argument("--top-k", type=_positive_int, default=5)
    _add_connection_options(query, defaults=False)

    document = commands.add_parser("document", help="Get one document summary")
    document.add_argument("doc_id")
    _add_connection_options(document, defaults=False)

    upload = commands.add_parser("upload", help="Upload one supported file for ingestion")
    upload.add_argument("path", type=Path)
    upload.add_argument("--collection", default="default")
    upload.add_argument("--force", action="store_true")
    upload.add_argument("--ai-enrichment", action="store_true")
    _add_connection_options(upload, defaults=False)

    job = commands.add_parser("job", help="Get one ingestion job")
    job.add_argument("job_id")
    _add_connection_options(job, defaults=False)
    return parser


def _add_connection_options(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    default_url: str | object = (
        os.environ.get("RAG_MCP_URL", DEFAULT_MCP_URL) if defaults else argparse.SUPPRESS
    )
    default_value: None | object = None if defaults else argparse.SUPPRESS
    parser.add_argument("--url", type=_mcp_url, default=default_url, help="MCP endpoint URL")
    parser.add_argument("--request-id", default=default_value)
    parser.add_argument("--actor-key", default=default_value)
    parser.add_argument("--session-id", default=default_value)


def _mcp_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("must be an absolute HTTP(S) URL")
    return value


def _positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return result


def _read_upload(path: Path) -> bytes:
    resolved = path.expanduser()
    if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise _CliInputError(f"unsupported file type: {resolved.suffix or '<none>'}")
    try:
        size = resolved.stat().st_size
        if not resolved.is_file():
            raise _CliInputError("upload path must be a file")
        if size > MAX_UPLOAD_BYTES:
            raise _CliInputError("file exceeds the 50 MiB upload limit")
        content = resolved.read_bytes()
    except _CliInputError:
        raise
    except OSError as exc:
        raise _CliInputError(f"cannot read upload path: {exc}") from exc
    if not content:
        raise _CliInputError("upload file must not be empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise _CliInputError("file exceeds the 50 MiB upload limit")
    return content


if __name__ == "__main__":
    raise SystemExit(main())
