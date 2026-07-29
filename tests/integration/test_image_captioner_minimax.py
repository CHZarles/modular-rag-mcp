from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from core.settings import load_settings
from core.types import Chunk
from ingestion.transform import ImageCaptioner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LLM_INTEGRATION") != "1",
        reason="set RUN_LLM_INTEGRATION=1 to call the configured multimodal LLM",
    ),
]

_SETTINGS_PATH = Path(__file__).parents[2] / "config/settings.yaml"


def _make_flow_diagram(path: Path) -> Path:
    """生成包含明确文字和流程关系的图片，便于验证真实视觉理解。"""
    with pymupdf.open() as document:
        page = document.new_page(width=640, height=240)
        page.insert_text((40, 50), "RAG INGESTION FLOW", fontsize=24)
        labels = ["PDF", "CHUNKS", "VECTOR INDEX"]
        for index, label in enumerate(labels):
            x = 40 + index * 200
            page.draw_rect(pymupdf.Rect(x, 100, x + 140, 160), width=2)
            page.insert_text((x + 20, 135), label, fontsize=16)
            if index < len(labels) - 1:
                page.draw_line((x + 140, 130), (x + 190, 130), width=2)
        page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(path)
    return path


def test_minimax_m3_captions_image_through_openai_compatible_api(tmp_path: Path) -> None:
    image_path = _make_flow_diagram(tmp_path / "rag-flow.png")
    chunk = Chunk(
        id="vision-integration",
        text="The following diagram shows the ingestion flow.\n\n[IMAGE: flow-1]",
        metadata={
            "source_path": "rag-guide.pdf",
            "image_refs": ["flow-1"],
            "images": [
                {"id": "flow-1", "path": str(image_path), "mime_type": "image/png"}
            ],
        },
        source_ref="rag-guide",
        chunk_index=0,
    )

    result = ImageCaptioner(load_settings(str(_SETTINGS_PATH))).transform([chunk])[0]

    print(f"\nMiniMax image caption: {result.metadata.get('image_captions')}")
    assert result.metadata["image_captioned_by"] == "vision_llm"
    assert result.metadata["has_unprocessed_images"] is False
    caption = result.metadata["image_captions"]["flow-1"]
    assert caption
    assert "<think>" not in caption.lower()
    searchable_text = result.text.lower()
    assert "[图片描述:" in result.text
    assert any(term in searchable_text for term in ("rag", "pdf", "chunk", "vector"))
