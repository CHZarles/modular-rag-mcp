"""Dashboard pages."""

from src.observability.dashboard.pages.data_browser import render as render_data_browser
from src.observability.dashboard.pages.ingestion_manager import (
    render as render_ingestion_manager,
)
from src.observability.dashboard.pages.ingestion_traces import (
    render as render_ingestion_traces,
)
from src.observability.dashboard.pages.overview import render as render_overview

__all__ = [
    "render_data_browser",
    "render_ingestion_manager",
    "render_ingestion_traces",
    "render_overview",
]
