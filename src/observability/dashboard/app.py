"""Streamlit entry point for the local RAG operations dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.observability.dashboard.pages.data_browser import (  # noqa: E402
    render as render_data_browser,
)
from src.observability.dashboard.pages.evaluation_panel import (  # noqa: E402
    render as render_evaluation_panel,
)
from src.observability.dashboard.pages.ingestion_manager import (  # noqa: E402
    render as render_ingestion_manager,
)
from src.observability.dashboard.pages.ingestion_traces import (  # noqa: E402
    render as render_ingestion_traces,
)
from src.observability.dashboard.pages.overview import render as render_overview  # noqa: E402
from src.observability.dashboard.pages.query_traces import (  # noqa: E402
    render as render_query_traces,
)


def main() -> None:
    st.set_page_config(
        page_title="Modular RAG Console",
        page_icon=":material/hub:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _apply_styles()
    st.logo(
        ":material/hub:",
        icon_image=":material/hub:",
        size="large",
    )

    navigation = st.navigation(
        {
            "Workspace": [
                st.Page(
                    render_overview,
                    title="系统总览",
                    icon=":material/dashboard:",
                    url_path="overview",
                    default=True,
                ),
                st.Page(
                    render_data_browser,
                    title="数据浏览器",
                    icon=":material/database:",
                    url_path="data-browser",
                ),
                st.Page(
                    render_ingestion_manager,
                    title="Ingestion 管理",
                    icon=":material/upload_file:",
                    url_path="ingestion",
                ),
            ],
            "Observability": [
                st.Page(
                    render_ingestion_traces,
                    title="Ingestion 追踪",
                    icon=":material/account_tree:",
                    url_path="ingestion-traces",
                ),
                st.Page(
                    render_query_traces,
                    title="Query 追踪",
                    icon=":material/search_insights:",
                    url_path="query-traces",
                ),
            ],
            "Quality": [
                st.Page(
                    render_evaluation_panel,
                    title="评估面板",
                    icon=":material/analytics:",
                    url_path="evaluation",
                ),
            ],
        },
        position="sidebar",
        expanded=True,
    )
    navigation.run()


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --rag-ink: #15221f;
            --rag-muted: #5d6b67;
            --rag-line: #dce5e2;
            --rag-paper: #f7f9f8;
            --rag-teal: #0f766e;
            --rag-blue: #2563eb;
            --rag-amber: #b45309;
        }
        .stApp { background: var(--rag-paper); color: var(--rag-ink); }
        .block-container { max-width: 1280px; padding-top: 2rem; padding-bottom: 3rem; }
        h1, h2, h3, p, button, label, input { letter-spacing: 0 !important; }
        h1 { color: var(--rag-ink); font-weight: 720; }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--rag-line);
            border-top: 3px solid var(--rag-teal);
            border-radius: 8px;
            min-height: 118px;
            padding: 1rem 1.1rem;
        }
        [data-testid="stMetricLabel"] { color: var(--rag-muted); }
        [data-testid="stMetricValue"] { color: var(--rag-ink); }
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff;
            border-color: var(--rag-line);
            border-radius: 8px;
            min-height: 190px;
        }
        .component-kicker {
            align-items: center;
            color: var(--rag-muted);
            display: flex;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.72rem;
            justify-content: space-between;
            letter-spacing: 0;
        }
        .component-state { font-weight: 700; }
        .component-state.ready { color: var(--rag-teal); }
        .component-state.off { color: var(--rag-amber); }
        [data-testid="stCode"] {
            border: 0;
            border-left: 3px solid var(--rag-blue);
            border-radius: 0 4px 4px 0;
        }
        @media (max-width: 700px) {
            .block-container { padding-top: 1.25rem; }
            [data-testid="stVerticalBlockBorderWrapper"] { min-height: auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
