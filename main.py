"""MCP Server entry point.

Responsibilities at this stage (DEV_SPEC §A1 / §A3):

1. Make ``src/`` importable when running directly from a checkout.
2. Build a logger and load ``config/settings.yaml``.
3. Validate required fields and fail-fast on misconfiguration.
4. Print a short readiness banner so operators can confirm boot.

The actual MCP server loop, knowledge service wiring, and CLI scripts
arrive in later stages (B / E / scripts/*).
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap: allow `python main.py` from a fresh checkout without
# requiring the package to be installed in editable mode.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"

if _SRC_DIR.is_dir():
    src_str = str(_SRC_DIR)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

from src.core.settings import (  # noqa: E402 — path tweak above must run first
    SettingsError,
    load_settings,
)
from src.observability.logger import get_logger  # noqa: E402

_LOG = get_logger("main")
_DEFAULT_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _resolve_settings_path() -> Path:
    """Locate ``config/settings.yaml``, honouring ``SETTINGS_PATH`` if set."""
    import os

    env_path = os.environ.get("SETTINGS_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return _DEFAULT_SETTINGS_PATH


def main() -> int:
    """Entry point: load + validate settings; log a readiness banner."""
    settings_path = _resolve_settings_path()
    try:
        settings = load_settings(settings_path)
    except FileNotFoundError as exc:
        _LOG.error("%s", exc)
        return 1
    except SettingsError as exc:
        _LOG.error("settings validation failed: %s", exc)
        return 2

    sections = (
        f"llm={settings.llm.provider}, "
        f"embedding={settings.embedding.provider}, "
        f"vector_store={settings.vector_store.backend}, "
        f"rerank={settings.rerank.backend}, "
        f"knowledge_service={settings.knowledge_service.mode}"
    )
    _LOG.info("modular-rag-mcp-server skeleton ready (sections=%s)", sections)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())