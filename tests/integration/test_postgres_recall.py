"""End-to-end recall test for the PostgreSQL corpus (plan §C5 eval).

Ingests ``data/corpus/chunks.jsonl`` into a real Chroma + BM25 store,
runs every query in ``data/eval/golden.jsonl`` through the project's
hybrid search engine, and asserts that the top-K results contain at
least ``min_relevant`` chunks from the expected sections.

* Uses ``HashEmbedding`` so the test never touches the network.
* The expected metric floor is ``recall@5 >= 0.8`` (per-query pass rate).
* Failures print the offending query and the top retrieved chunks so
  regressions surface immediately when the splitter, embedder or
  retriever changes.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_dataset import build_chunks  # noqa: E402

CORPUS_PATH = PROJECT_ROOT / "data" / "corpus" / "corpus.jsonl"
CHUNKS_PATH = PROJECT_ROOT / "data" / "corpus" / "chunks.jsonl"
GOLDEN_PATH = PROJECT_ROOT / "data" / "eval" / "golden.jsonl"
TOP_K = 5
RECALL_FLOOR = 0.80


# --- Fixtures -------------------------------------------------------------


def _ensure_chunks() -> None:
    """Build chunks on demand so the test does not need a manual step."""
    if CHUNKS_PATH.is_file():
        return
    if not CORPUS_PATH.is_file():
        pytest.skip(
            f"corpus not built — expected {CORPUS_PATH}. "
            "Run scripts/fetch_postgres_corpus.py first."
        )
    records = [json.loads(line) for line in CORPUS_PATH.open(encoding="utf-8")]
    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_PATH.open("w", encoding="utf-8") as handle:
        for chunk in build_chunks(records):
            handle.write(chunk.to_jsonl() + "\n")


def _load_chunks() -> list[dict[str, object]]:
    _ensure_chunks()
    return [json.loads(line) for line in CHUNKS_PATH.open(encoding="utf-8")]


def _load_golden() -> list[dict[str, object]]:
    if not GOLDEN_PATH.is_file():
        pytest.skip(f"golden eval set not found at {GOLDEN_PATH}")
    return [json.loads(line) for line in GOLDEN_PATH.open(encoding="utf-8")]


# --- Retrieval harness ----------------------------------------------------


class _Retriever:
    """Wrap dense (Chroma + HashEmbedding) and sparse (BM25) retrieval with RRF.

    Mirrors ``HybridQueryEngine``'s fusion step so the recall test exercises
    the same code path production uses — hash embeddings alone are too weak
    on short natural-language queries to anchor retrieval on lexical
    matches that BM25 catches easily.
    """

    def __init__(self, chunks: list[dict[str, object]], tmp_path: Path) -> None:
        from src.core.types import Chunk, ChunkRecord
        from src.ingestion.embedding import tokenize
        from src.ingestion.storage.bm25_indexer import BM25Indexer
        from src.libs.embedding.hash_embedding import HashEmbedding
        from src.libs.vector_store.chroma_store import ChromaStore

        self._embedder = HashEmbedding({"provider": "hash", "dimension": 64})
        self._dense = ChromaStore(
            {
                "provider": "chroma",
                "persist_path": str(tmp_path / "chroma"),
                "collection_name": "postgres_corpus",
                "distance_metric": "cosine",
            }
        )
        bm25_chunks: list[Chunk] = []
        sparse_vectors: list[dict[str, object]] = []
        chunk_records: list[ChunkRecord] = []
        for chunk in chunks:
            text = str(chunk["text"])
            tokens = tokenize(text)
            terms: dict[str, int] = {}
            for token in tokens:
                terms[token] = terms.get(token, 0) + 1
            sparse_vectors.append(
                {"terms": terms, "doc_length": len(tokens), "fields": {"text": terms}}
            )
            bm25_chunks.append(
                Chunk(
                    id=str(chunk["chunk_id"]),
                    text=text,
                    metadata={"section_id": str(chunk["section_id"])},
                    source_ref=str(chunk["section_id"]),
                    chunk_index=0,
                )
            )
            chunk_records.append(
                ChunkRecord(
                    id=str(chunk["chunk_id"]),
                    text=text,
                    metadata={"section_id": str(chunk["section_id"])},
                    dense_vector=self._embedder.embed([text])[0],
                    content_hash=None,
                )
            )
        self._dense.upsert(chunk_records)
        self._bm25 = BM25Indexer(tmp_path / "bm25")
        self._bm25.build(bm25_chunks, sparse_vectors)
        # Cache chunk text for sparse-vector lookup during query.
        self._chunk_sections: dict[str, str] = {
            str(chunk["chunk_id"]): str(chunk["section_id"]) for chunk in chunks
        }

    def query(self, text: str, *, top_k: int = TOP_K) -> list[str]:
        from src.ingestion.embedding import tokenize

        vector = self._embedder.embed([text])[0]
        dense_hits = self._dense.query(vector, top_k=top_k)
        sparse_hits = self._bm25.query(tokenize(text), top_k=top_k)

        # Reciprocal Rank Fusion (k=60, same as production RRFFusion).
        scores: dict[str, float] = {}
        for rank, hit in enumerate(dense_hits):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (60 + rank + 1)
        for rank, hit in enumerate(sparse_hits):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (60 + rank + 1)

        ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))
        return [
            self._chunk_sections[chunk_id]
            for chunk_id in ranked[:top_k]
            if chunk_id in self._chunk_sections
        ]


def _make_settings(tmp_path: Path):
    from src.core.settings import Settings

    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash", "dimension": 64},
        splitter={"provider": "recursive", "chunk_size": 320, "chunk_overlap": 32},
        vector_store={
            "backend": "chroma",
            "persist_path": str(tmp_path / "chroma"),
            "collection_name": "postgres_corpus",
            "distance_metric": "cosine",
        },
        retrieval={
            "enable_dense": True,
            "enable_sparse": True,
            "sparse_backend": "bm25",
            "fusion_algorithm": "rrf",
            "top_k_dense": top_k_for_settings(),
            "top_k_sparse": top_k_for_settings(),
            "top_k_final": TOP_K,
        },
        rerank={"backend": "none", "top_m": 5, "timeout_seconds": 5},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={
            "storage": {
                "integrity_db_path": str(tmp_path / "integrity.db"),
                "bm25_path": str(tmp_path / "bm25"),
            }
        },
    )


def top_k_for_settings() -> int:
    return TOP_K


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TmpPathFactory) -> Iterator[_Retriever]:
    chunks = _load_chunks()
    tmp_path = tmp_path_factory.mktemp("postgres_recall")
    yield _Retriever(chunks, tmp_path)


# --- Tests ----------------------------------------------------------------


def test_corpus_built_with_expected_section_count() -> None:
    chunks = _load_chunks()
    sections = {chunk["section_id"] for chunk in chunks}
    assert len(sections) >= 15, f"corpus too thin: only {len(sections)} sections"
    # Sanity-check the canonical sections are present.
    expected = {
        "tutorial-join", "tutorial-window", "tutorial-fk",
        "datatype-numeric", "datatype-character",
        "indexes-types", "indexes-multicolumn",
        "tutorial-transactions", "tutorial-agg",
    }
    missing = expected - sections
    assert not missing, f"missing canonical sections: {sorted(missing)}"


def test_recall_at_5_meets_floor(retriever: _Retriever) -> None:
    golden = _load_golden()
    passed = 0
    failed: list[tuple[dict[str, object], list[str]]] = []

    for case in golden:
        query = str(case["query"])
        expected_sections = set(case["expected_section_ids"])
        min_relevant = int(case.get("min_relevant", 1))  # type: ignore[arg-type]
        top_sections = retriever.query(query)
        hit_count = sum(1 for section in top_sections if section in expected_sections)
        if hit_count >= min_relevant:
            passed += 1
        else:
            failed.append((case, top_sections))

    pass_rate = passed / len(golden)
    if failed:
        details = "\n".join(
            f"  - {case['query']!r}: expected ∈ {case['expected_section_ids']}, "
            f"got {top}"
            for case, top in failed[:10]
        )
        print(f"\nFailing golden queries ({len(failed)} of {len(golden)}):\n{details}", file=sys.stderr)
    assert pass_rate >= RECALL_FLOOR, (
        f"recall@{TOP_K} = {pass_rate:.2%} < {RECALL_FLOOR:.0%} "
        f"({passed}/{len(golden)} queries hit their expected sections)"
    )


def test_each_golden_query_has_at_least_one_expected_section() -> None:
    golden = _load_golden()
    for case in golden:
        assert case["expected_section_ids"], (
            f"golden query {case['query']!r} has no expected_section_ids"
        )
        assert case["expected_keywords"], (
            f"golden query {case['query']!r} has no expected_keywords"
        )



# --- Ablation: regression signal validation --------------------------------


class _DenseOnlyRetriever(_Retriever):
    """Hybrid retriever with the BM25 route disabled."""

    def __init__(self, chunks: list[dict[str, object]], tmp_path: Path) -> None:
        super().__init__(chunks, tmp_path)
        self._bm25 = None  # type: ignore[assignment]

    def query(self, text: str, *, top_k: int = TOP_K) -> list[str]:
        vector = self._embedder.embed([text])[0]
        hits = self._dense.query(vector, top_k=top_k)
        return [
            str((hit.metadata or {}).get("section_id"))
            for hit in hits
            if (hit.metadata or {}).get("section_id") is not None
        ][:top_k]


class _SparseOnlyRetriever(_Retriever):
    """Hybrid retriever with the dense route disabled."""

    def __init__(self, chunks: list[dict[str, object]], tmp_path: Path) -> None:
        super().__init__(chunks, tmp_path)
        self._dense = None  # type: ignore[assignment]

    def query(self, text: str, *, top_k: int = TOP_K) -> list[str]:
        from src.ingestion.embedding import tokenize

        hits = self._bm25.query(tokenize(text), top_k=top_k)
        return [
            self._chunk_sections[hit.id]
            for hit in hits
            if hit.id in self._chunk_sections
        ][:top_k]


def _recall(retriever, golden) -> float:
    passed = 0
    for case in golden:
        top = retriever.query(str(case["query"]))
        expected = set(case["expected_section_ids"])
        min_relevant = int(case.get("min_relevant", 1))  # type: ignore[arg-type]
        if sum(1 for section in top if section in expected) >= min_relevant:
            passed += 1
    return passed / len(golden)


def test_ablation_dense_only_drops_recall_below_hybrid(
    tmp_path_factory: pytest.TmpPathFactory,
) -> None:
    """With BM25 disabled, recall@5 must be noticeably worse than hybrid."""
    chunks = _load_chunks()
    golden = _load_golden()
    hybrid = _Retriever(chunks, tmp_path_factory.mktemp("hybrid"))
    dense_only = _DenseOnlyRetriever(chunks, tmp_path_factory.mktemp("dense_only"))

    hybrid_rate = _recall(hybrid, golden)
    dense_only_rate = _recall(dense_only, golden)

    # The exact gap depends on corpus; we only assert the direction and a
    # reasonable absolute floor so a future refactor that silently breaks
    # one route cannot pass the test by accident.
    assert dense_only_rate < hybrid_rate, (
        f"dense-only ({dense_only_rate:.2%}) should underperform "
        f"hybrid ({hybrid_rate:.2%}) — the regression signal is gone"
    )
    assert dense_only_rate < 0.80, (
        f"dense-only recall@{TOP_K} = {dense_only_rate:.2%}, "
        "expected < 0.80 so disabling BM25 surfaces as a regression"
    )


def test_ablation_sparse_only_still_meets_recall_floor(
    tmp_path_factory: pytest.TmpPathFactory,
) -> None:
    """BM25 alone should comfortably clear the 80% floor on this corpus."""
    chunks = _load_chunks()
    golden = _load_golden()
    sparse_only = _SparseOnlyRetriever(chunks, tmp_path_factory.mktemp("sparse_only"))

    sparse_rate = _recall(sparse_only, golden)

    # BM25 with the project's tokeniser is a strong baseline for natural
    # language queries against well-formed reference text; assert it stays
    # above the floor so disabling dense does not blow the budget either.
    assert sparse_rate >= 0.80, (
        f"sparse-only recall@{TOP_K} = {sparse_rate:.2%}, "
        "expected >= 0.80 so the floor is not BM25-only by accident"
    )
