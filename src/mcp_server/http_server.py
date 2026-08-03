"""HTTP wrapper that exposes the stdio MCP server over Streamable HTTP.

The MCP server itself is the same one used by the stdio transport; this module
just mounts ``server.streamable_http_app`` behind Uvicorn so remote agents can
connect over HTTP instead of spawning a subprocess.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from src.mcp_server.server import create_mcp_server

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
STREAMABLE_HTTP_PATH = "/mcp"


def build_app():
    server = create_mcp_server()
    return server.streamable_http_app(streamable_http_path=STREAMABLE_HTTP_PATH)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if "RAG_SETTINGS_PATH" not in os.environ:
        os.environ["RAG_SETTINGS_PATH"] = str(PROJECT_ROOT / "config" / "settings.yaml")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the MCP server over Streamable HTTP")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
