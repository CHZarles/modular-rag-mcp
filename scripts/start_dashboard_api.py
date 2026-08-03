"""Launch the FastAPI dashboard API bound to localhost."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from src.observability.dashboard.api import create_app  # noqa: E402
from src.observability.dashboard.services.config_service import DEFAULT_SETTINGS_PATH  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings_path = Path(args.settings).expanduser().resolve()
    app = create_app(settings_path=settings_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the local RAG dashboard HTTP API")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
