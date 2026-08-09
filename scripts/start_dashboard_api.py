"""Launch the FastAPI dashboard API bound to localhost."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_INDEX = PROJECT_ROOT / "web" / "dist" / "index.html"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from src.observability.dashboard.api import create_app  # noqa: E402
from src.observability.dashboard.services.config_service import (  # noqa: E402
    DEFAULT_SETTINGS_PATH,
    ConfigService,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not WEB_INDEX.is_file():
        print(
            "Dashboard frontend is not built. Run: npm ci --prefix web && "
            "npm --prefix web run build",
            file=sys.stderr,
        )
        return 2
    settings_path = Path(args.settings).expanduser().resolve()
    try:
        options = ConfigService.from_path(settings_path).dashboard_options()
    except Exception as exc:
        print(f"Dashboard configuration failed: {exc}", file=sys.stderr)
        return 2
    if not options.enabled:
        print("Dashboard is disabled by configuration.", file=sys.stderr)
        return 2
    app = create_app(settings_path=settings_path)
    uvicorn.run(
        app,
        host=args.host or options.address,
        port=args.port or options.port,
        log_level="info",
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the local RAG dashboard HTTP API")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
