"""SparseRetriever 的 BM25 编排与结果转换测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.query_engine import SparseRetriever
from src.core.types import Chunk, JsonDict, RetrievalCandidate, RetrievalResult, SearchHit
from src.ingestion.embedding import SparseEncoder
from src.ingestion.storage import BM25Indexer
from src.ports.query import SparseRetriever as SparseRetrieverPort


class FakeBM25Store:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[list[str], int, JsonDict | None, object | None]] = []

    def upsert(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[JsonDict],
        trace: Any | None = None,
    ) -> None:
        return None

    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        self.calls.append((keywords, top_k, filters, trace))
        return self.hits

    def remove_document(self, source_path: str, collection: str) -> None:
        return None

    def remove_generation(self, doc_key: str, generation: int) -> int:
        return 0


def test_retrieve_queries_bm25_and_converts_hits() -> None:
    trace = object()
    filters = {"collection": "docs", "doc_type": "pdf"}
    first_metadata = {"collection": "docs", "page": 2}
    store = FakeBM25Store(
        [
            SearchHit(
                id="chunk-1",
                text="A lease protects one ingestion generation.",
                metadata=first_metadata,
                score=2.75,
                score_kind="bm25",
                raw={"matched_terms": ["lease"], "doc_length": 8},
            ),
            SearchHit(
                id="chunk-2",
                text="A second result that should be truncated.",
                metadata={"collection": "docs", "page": 4},
                score=1.1,
                score_kind="bm25",
            ),
        ]
    )

    results = SparseRetriever(store).retrieve(
        ["lease", "generation"],
        top_k=1,
        filters=filters,
        trace=trace,
    )

    assert store.calls == [(["lease", "generation"], 1, filters, trace)]
    assert results == [
        RetrievalCandidate(
            chunk_id="chunk-1",
            text="A lease protects one ingestion generation.",
            metadata={"collection": "docs", "page": 2},
            score=2.75,
            source="sparse",
            rank=1,
            debug={
                "score_kind": "bm25",
                "raw": {"matched_terms": ["lease"], "doc_length": 8},
            },
        )
    ]
    assert isinstance(results[0], RetrievalResult)

    # 返回对象不得把后续修改泄漏回持久化索引的原始命中。
    results[0].metadata["page"] = 99
    result_raw = results[0].debug["raw"]
    assert isinstance(result_raw, dict)
    result_raw["doc_length"] = 99
    assert first_metadata["page"] == 2
    assert store.hits[0].raw["doc_length"] == 8


def test_retrieve_with_real_bm25_index_returns_text_and_metadata(tmp_path) -> None:
    chunks = [
        _chunk("lease", "generation lease state machine", page=1),
        _chunk("vector", "dense vector similarity", page=2),
    ]
    indexer = BM25Indexer(tmp_path / "bm25")
    indexer.build(chunks, SparseEncoder().encode(chunks))

    results = SparseRetriever(indexer).retrieve(
        ["generation", "lease"],
        top_k=2,
        filters={"collection": "docs"},
    )

    assert [result.chunk_id for result in results] == ["lease"]
    assert results[0].text == "generation lease state machine"
    assert results[0].metadata == {"collection": "docs", "page": 1}
    assert results[0].debug["score_kind"] == "bm25"


def test_retrieve_without_keywords_skips_bm25() -> None:
    store = FakeBM25Store([])

    assert SparseRetriever(store).retrieve([], top_k=3) == []
    assert store.calls == []


@pytest.mark.parametrize("top_k", [0, -1])
def test_retrieve_rejects_non_positive_top_k(top_k: int) -> None:
    retriever = SparseRetriever(FakeBM25Store([]))

    with pytest.raises(ValueError, match="top_k must be positive"):
        retriever.retrieve(["lease"], top_k=top_k)


def test_sparse_retriever_matches_port_contract() -> None:
    assert isinstance(SparseRetriever(FakeBM25Store([])), SparseRetrieverPort)


def _chunk(chunk_id: str, text: str, page: int) -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"collection": "docs", "page": page},
        source_ref="manual.pdf",
        chunk_index=page - 1,
    )
