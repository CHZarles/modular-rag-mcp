"""Dashboard pages."""

from src.observability.dashboard.pages.data_browser import render as render_data_browser
from src.observability.dashboard.pages.evaluation_panel import render as render_evaluation_panel
from src.observability.dashboard.pages.ingestion_manager import (
    render as render_ingestion_manager,
)
from src.observability.dashboard.pages.ingestion_traces import (
    render as render_ingestion_traces,
)
from src.observability.dashboard.pages.overview import render as render_overview
from src.observability.dashboard.pages.query_traces import render as render_query_traces

__all__ = [
    "render_data_browser",
    "render_evaluation_panel",
    "render_ingestion_manager",
    "render_ingestion_traces",
    "render_overview",
    "render_query_traces",
]
