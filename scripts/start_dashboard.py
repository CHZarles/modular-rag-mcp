"""Launch the local Streamlit dashboard with project settings."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.observability.dashboard.services.config_service import (  # noqa: E402
    DEFAULT_SETTINGS_PATH,
    ConfigService,
)

APP_PATH = PROJECT_ROOT / "src" / "observability" / "dashboard" / "app.py"


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings_path = Path(args.settings).expanduser().resolve()
    try:
        options = ConfigService.from_path(settings_path).dashboard_options()
    except Exception as exc:
        print(f"Dashboard configuration failed: {exc}", file=sys.stderr)
        return 2
    if not options.enabled:
        print("Dashboard is disabled by configuration.", file=sys.stderr)
        return 2

    port = args.port or options.port
    address = args.address or options.address
    command = _streamlit_command(port=port, address=address)
    environment = {**os.environ, "RAG_SETTINGS_PATH": str(settings_path)}
    try:
        return subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=False).returncode
    except KeyboardInterrupt:
        return 130


def _streamlit_command(*, port: int, address: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_PATH),
        "--server.address",
        address,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the local RAG dashboard")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH))
    parser.add_argument("--address", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
