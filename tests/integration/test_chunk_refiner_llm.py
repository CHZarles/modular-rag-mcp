from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from core.settings import load_settings
from core.types import Chunk
from ingestion.transform import ChunkRefiner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LLM_INTEGRATION") != "1",
        reason="set RUN_LLM_INTEGRATION=1 to call the configured LLM",
    ),
]

_SETTINGS_PATH = Path(__file__).parents[2] / "config/settings.yaml"


def _chunk(text: str) -> Chunk:
    return Chunk(
        id="integration_chunk",
        text=text,
        metadata={"source_path": "integration.md"},
        source_ref="integration",
        chunk_index=0,
    )


def test_configured_openai_compatible_llm_refines_real_text() -> None:
    settings = load_settings(str(_SETTINGS_PATH))
    original = "[页眉] Export\n\nRAG combines retrieval with generation.\n\nPage 1 of 1"

    result = ChunkRefiner(settings).transform([_chunk(original)])[0]

    print(f"\nLLM refinement result:\n{result.text}")
    assert result.metadata["refined_by"] == "llm"
    assert "RAG" in result.text
    assert "retrieval" in result.text.lower()
    assert "generation" in result.text.lower()
    assert "Page 1 of 1" not in result.text
    assert "<think>" not in result.text.lower()


def test_invalid_model_falls_back_to_rule_result() -> None:
    settings = load_settings(str(_SETTINGS_PATH))
    invalid_llm = {**settings.llm, "model": "invalid-model-for-refiner-test"}
    invalid_settings = replace(settings, llm=invalid_llm)

    result = ChunkRefiner(invalid_settings).transform(
        [_chunk("Page 1 of 2\n\nUseful   text.")]
    )[0]

    assert result.text == "Useful text."
    assert result.metadata["refined_by"] == "rule"
    assert result.metadata["refinement_fallback_reason"] == "llm_refinement_failed"
