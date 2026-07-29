from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from core.settings import load_settings
from core.types import Chunk
from ingestion.transform import MetadataEnricher

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
        id="metadata_integration_chunk",
        text=text,
        metadata={"source_path": "retrieval-guide.md"},
        source_ref="integration",
        chunk_index=0,
    )


def test_configured_openai_compatible_llm_generates_semantic_metadata() -> None:
    settings = load_settings(str(_SETTINGS_PATH))
    chunk = _chunk(
        "# Hybrid Retrieval\n\n"
        "Hybrid retrieval combines dense semantic search with BM25 keyword matching. "
        "Reciprocal rank fusion merges both ranked result lists."
    )

    result = MetadataEnricher(settings).transform([chunk])[0]

    print(f"\nLLM metadata result: {result.metadata}")
    assert result.metadata["metadata_enriched_by"] == "llm"
    assert result.metadata["title"]
    assert result.metadata["summary"]
    assert result.metadata["tags"]
    semantic_text = " ".join(
        [
            str(result.metadata["title"]),
            str(result.metadata["summary"]),
            " ".join(result.metadata["tags"]),
        ]
    ).lower()
    assert "bm25" in semantic_text
    assert "dense" in semantic_text
    assert "<think>" not in semantic_text


def test_invalid_model_falls_back_to_rule_metadata() -> None:
    settings = load_settings(str(_SETTINGS_PATH))
    invalid_settings = replace(
        settings,
        llm={**settings.llm, "model": "invalid-model-for-metadata-enricher-test"},
    )

    result = MetadataEnricher(invalid_settings).transform(
        [_chunk("# Stable Title\n\nUseful content.")]
    )[0]

    assert result.metadata["title"] == "Stable Title"
    assert result.metadata["summary"]
    assert result.metadata["tags"]
    assert result.metadata["metadata_enriched_by"] == "rule"
    assert result.metadata["metadata_enrichment_fallback_reason"] == "llm_enrichment_failed"
