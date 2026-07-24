"""MCP Server entry point.

This script is the default process entry for the Modular RAG MCP Server.
It performs three things — and only three things — at this stage of the
project (see DEV_SPEC §A1 / §A3):

1. Make the ``src/`` package importable when the project is run directly
   from a checkout (no ``pip install -e .`` required).
2. Validate that the configuration file at ``config/settings.yaml`` exists
   and is well-formed YAML.
3. Print a short readiness banner so operators can confirm the skeleton
   starts cleanly.

The actual MCP server loop, knowledge service wiring, and CLI scripts
arrive in later stages (B / E / scripts/*).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap: allow `python main.py` from a fresh checkout without
# requiring the package to be installed in the active environment.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"

if _SRC_DIR.is_dir():
    src_str = str(_SRC_DIR)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

# Now we can import from src.* and the top-level packages under src/.
import yaml  # local import after sys.path tweak is unnecessary; keep top-level


def _resolve_settings_path() -> Path:
    """Locate ``config/settings.yaml`` relative to the project root.

    Honours the ``SETTINGS_PATH`` environment variable so tests and CI can
    point at fixtures without monkey-patching source code.
    """

    env_path = os.environ.get("SETTINGS_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (_PROJECT_ROOT / "config" / "settings.yaml").resolve()


def main() -> int:
    """Entry point: load settings, fail-fast on missing keys, log readiness."""

    settings_path = _resolve_settings_path()
    if not settings_path.is_file():
        print(f"[main] ERROR: settings file not found: {settings_path}", file=sys.stderr)
        return 1

    try:
        with settings_path.open("r", encoding="utf-8") as fh:
            settings = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print(f"[main] ERROR: invalid YAML in {settings_path}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(settings, dict):
        print(
            f"[main] ERROR: top-level YAML in {settings_path} must be a mapping",
            file=sys.stderr,
        )
        return 1

    # Stage A only checks that the skeleton boots. Concrete field-level
    # validation belongs to A3 (Settings dataclass + validate_settings).
    top_level_keys = sorted(settings.keys())
    print(
        "[main] modular-rag-mcp-server skeleton ready "
        f"(settings={settings_path}, sections={top_level_keys})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())