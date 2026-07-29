from __future__ import annotations

import pytest

from core.types import Chunk
from ingestion.embedding import SparseEncoder, tokenize
from ports.ingestion import SparseEncoder as SparseEncoderProtocol


def make_chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        id=f"chunk-{index}",
        text=text,
        metadata={"source_path": "retrieval.md"},
        source_ref="document",
        chunk_index=index,
    )


def test_encode_returns_bm25_term_frequencies_and_document_length() -> None:
    chunk = make_chunk("RAG rag combines BM25 retrieval; retrieval stays exact.")

    result = SparseEncoder().encode([chunk])

    assert result == [
        {
            "terms": {
                "rag": 2,
                "combines": 1,
                "bm25": 1,
                "retrieval": 2,
                "stays": 1,
                "exact": 1,
            },
            "doc_length": 8,
        }
    ]
    assert chunk.metadata == {"source_path": "retrieval.md"}


def test_tokenize_normalizes_width_case_and_technical_words() -> None:
    assert tokenize("ＯｐｅｎＡＩ OpenAI-compatible vector_index") == [
        "openai",
        "openai-compatible",
        "vector_index",
    ]


def test_tokenize_uses_overlapping_bigrams_for_chinese_search() -> None:
    assert tokenize("混合检索") == ["混合", "合检", "检索"]
    assert tokenize("图") == ["图"]


@pytest.mark.parametrize("text", ["", " \n\t ", "--- ***"])
def test_encode_empty_or_marker_only_text_has_explicit_empty_statistics(text: str) -> None:
    assert SparseEncoder().encode([make_chunk(text)]) == [{"terms": {}, "doc_length": 0}]


def test_encode_preserves_chunk_order_and_returns_one_result_per_chunk() -> None:
    chunks = [make_chunk("alpha alpha", 0), make_chunk("beta", 1)]

    result = SparseEncoder().encode(chunks)

    assert result == [
        {"terms": {"alpha": 2}, "doc_length": 2},
        {"terms": {"beta": 1}, "doc_length": 1},
    ]


def test_encode_empty_batch_returns_empty_list() -> None:
    assert SparseEncoder().encode([]) == []


def test_sparse_encoder_matches_ingestion_protocol() -> None:
    assert isinstance(SparseEncoder(), SparseEncoderProtocol)
