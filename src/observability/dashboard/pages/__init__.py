"""Dashboard pages."""

from src.observability.dashboard.pages.data_browser import render as render_data_browser
from src.observability.dashboard.pages.overview import render as render_overview

__all__ = ["render_data_browser", "render_overview"]
