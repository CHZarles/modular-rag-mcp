"""Smoke tests: verify that the A1 package import contract holds.

These tests are intentionally minimal. Their only job is to fail loudly
if a future refactor accidentally breaks the public package surface
defined in DEV_SPEC §5.2 / §A1 acceptance.
"""

from __future__ import annotations

import importlib


def test_mcp_server_importable() -> None:
    """The MCP Server entry package must be importable as a top-level module."""
    assert importlib.import_module("mcp_server") is not None


def test_core_importable() -> None:
    """The Core (contracts / query engine / response) package must be importable."""
    assert importlib.import_module("core") is not None


def test_ingestion_importable() -> None:
    """The Ingestion Pipeline package must be importable."""
    assert importlib.import_module("ingestion") is not None


def test_libs_importable() -> None:
    """The pluggable Libs package must be importable."""
    assert importlib.import_module("libs") is not None


def test_observability_importable() -> None:
    """The Observability package must be importable."""
    assert importlib.import_module("observability") is not None


def test_main_module_compiles() -> None:
    """``main.py`` must be syntactically valid and importable as a script.

    We compile it in isolation (no execution) to guard against syntax drift
    without triggering the runtime side-effects of actually running main().
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    main_path = project_root / "main.py"
    assert main_path.is_file(), "main.py must exist at the project root"
    compile(main_path.read_text(encoding="utf-8"), str(main_path), "exec")