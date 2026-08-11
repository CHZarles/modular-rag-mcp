"""HTTP wrapper that exposes the stdio MCP server over Streamable HTTP.

The MCP server itself is the same one used by the stdio transport; this module
just mounts ``server.streamable_http_app`` behind Uvicorn so remote agents can
connect over HTTP instead of spawning a subprocess. It also exposes the
``/health/live`` and ``/health/ready`` endpoints mandated by plan §5.7.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from starlette.responses import JSONResponse

from src.core.services.knowledge_service import KnowledgeService
from src.core.trace import SQLiteTraceStore
from src.mcp_server.readiness import ReadinessService
from src.mcp_server.server import create_mcp_server
from src.mcp_server.tools.ingestion_jobs import MAX_BASE64_CHARS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
STREAMABLE_HTTP_PATH = "/mcp"
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
MAX_MCP_REQUEST_BODY_BYTES = MAX_BASE64_CHARS + 64 * 1024


def build_app(
    *,
    knowledge_service: KnowledgeService | None = None,
    trace_store: SQLiteTraceStore | None = None,
    settings_path: str | Path | None = None,
) -> Any:
    """Build the MCP HTTP app with optional dependency injection for tests.

    ``knowledge_service`` and ``trace_store`` are forwarded to the MCP server
    and the readiness probe respectively. ``settings_path`` selects the YAML
    the readiness probe parses; defaults to the project-root settings.yaml.
    """
    server = create_mcp_server(knowledge_service=knowledge_service)
    mcp_app = server.streamable_http_app(
        streamable_http_path=STREAMABLE_HTTP_PATH,
        max_request_body_size=MAX_MCP_REQUEST_BODY_BYTES,
    )

    resolved_settings = (
        Path(settings_path).expanduser()
        if settings_path is not None
        else Path(os.environ.get("RAG_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH))).expanduser()
    )
    readiness = ReadinessService(
        settings_path=resolved_settings,
        trace_store_probe=_trace_store_probe_for(trace_store),
    )
    return _wrap_with_health(mcp_app, readiness)


def _wrap_with_health(app: Any, readiness: ReadinessService) -> Any:
    """Mount ``/health/*`` routes alongside the MCP Streamable HTTP app."""
    from starlette.routing import Route

    async def live(_request: Any) -> JSONResponse:
        # Plan §5.7: liveness never builds the knowledge service.
        return JSONResponse({"status": "ok"})

    async def ready(_request: Any) -> JSONResponse:
        result = readiness.check()
        return JSONResponse(result.to_wire(), status_code=result.http_status)

    health_routes = [
        Route("/health/live", live, methods=["GET"]),
        Route("/health/ready", ready, methods=["GET"]),
    ]
    # The MCP app is already a Starlette/FastAPI application; bolt the
    # health routes on top so a single Uvicorn deployment serves them.
    app.router.routes.extend(health_routes)
    return app


def _trace_store_probe_for(store: SQLiteTraceStore | None):
    """Return a probe that bypasses disk I/O when a store is injected."""
    if store is None:
        from src.mcp_server.readiness import _default_trace_store_probe
        return _default_trace_store_probe

    def _probe(_settings: Any):
        from src.mcp_server.readiness import (
            _CHECK_OK,
            _CHECK_STORE_UNWRITABLE,
            ReadinessCheck,
        )

        if store.check_writable():
            return ReadinessCheck("trace_store", _CHECK_OK)
        return ReadinessCheck("trace_store", _CHECK_STORE_UNWRITABLE)

    return _probe


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if "RAG_SETTINGS_PATH" not in os.environ:
        os.environ["RAG_SETTINGS_PATH"] = str(DEFAULT_SETTINGS_PATH)
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the MCP server over Streamable HTTP")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
