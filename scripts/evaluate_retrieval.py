"""Evaluate the production retrieval stack with the configured embedding provider."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_dataset import build_chunks  # noqa: E402
from src.core.query_engine import (  # noqa: E402
    DenseRetriever,
    ExactMetadataFilter,
    HybridQueryEngine,
    HybridSearchConfig,
    NoneReranker,
    QueryProcessor,
    RRFFusion,
    SparseRetriever,
)
from src.core.response import ResponseBuilder  # noqa: E402
from src.core.settings import load_settings, resolve_settings_path  # noqa: E402
from src.core.types import Chunk, ChunkRecord, QueryRequest, RetrievalCandidate  # noqa: E402
from src.ingestion.embedding import SparseEncoder  # noqa: E402
from src.ingestion.storage import BM25Indexer  # noqa: E402
from src.ingestion.transform import ImageCaptioner  # noqa: E402
from src.libs.embedding import create_embedding  # noqa: E402
from src.libs.splitter import create_splitter  # noqa: E402
from src.libs.vector_store import ChromaStore  # noqa: E402

DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_CORPUS = PROJECT_ROOT / "data" / "corpus" / "corpus.jsonl"
DEFAULT_GOLDEN = PROJECT_ROOT / "data" / "eval" / "golden.jsonl"
DEFAULT_HOTPOT_DIR = PROJECT_ROOT / "data" / "hotpotqa" / "benchmark"
TOP_K = 5
MIN_HIT_AT_5 = 0.90
MIN_MRR_AT_5 = 0.80
MIN_IMAGE_HIT_AT_5 = 1.0


@dataclass(frozen=True)
class _Case:
    query: str
    expected_sections: frozenset[str]
    kind: str = "text"
    relevant_metadata_key: str = "section_id"


_IMAGE_CASES = (
    (
        "image-ingestion-flow",
        "Which component follows CHUNKS in the ingestion flow?",
        ("RAG INGESTION FLOW", "PDF  ->  CHUNKS  ->  VECTOR INDEX"),
    ),
    (
        "image-query-flow",
        "What sits between the CLIENT and KNOWLEDGE INDEX?",
        ("QUERY ARCHITECTURE", "CLIENT  ->  MCP SERVER  ->  KNOWLEDGE INDEX"),
    ),
    (
        "image-recall-chart",
        "Which retrieval method has the highest recall in the chart?",
        ("RECALL AT FIVE", "BM25 100", "HYBRID 94", "DENSE 66"),
    ),
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate BM25, dense, and hybrid retrieval with real providers"
    )
    parser.add_argument("--settings", help="settings.yaml path")
    parser.add_argument(
        "--dataset",
        choices=("postgres", "hotpotqa"),
        default="postgres",
        help="text retrieval dataset to evaluate",
    )
    parser.add_argument("--skip-images", action="store_true", help="skip vision evaluation")
    args = parser.parse_args(argv)

    try:
        settings_path = resolve_settings_path(args.settings, default_path=DEFAULT_SETTINGS)
        settings = load_settings(str(settings_path))
        chunks, cases = _load_dataset(settings, args.dataset)
        with tempfile.TemporaryDirectory(prefix="rag-retrieval-eval-") as directory:
            root = Path(directory)
            if not args.skip_images:
                image_chunks, image_cases = _build_image_dataset(settings, root)
                chunks.extend(image_chunks)
                cases.extend(image_cases)
            report = _run(settings, chunks, cases, root, dataset=args.dataset)
    except Exception as exc:
        print(f"retrieval evaluation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _run(
    settings: Any,
    chunks: list[Chunk],
    cases: list[_Case],
    root: Path,
    *,
    dataset: str,
) -> dict[str, Any]:
    embedding = create_embedding(settings)
    vectors = _embed_batches(
        embedding,
        [chunk.text for chunk in chunks],
        int(settings.ingestion.get("batch_size", 32)),
    )
    sparse_vectors = SparseEncoder().encode(chunks)

    vector_store = ChromaStore(
        {
            "persist_path": str(root / "chroma"),
            "collection_name": "retrieval-evaluation",
            "distance_metric": settings.vector_store.get("distance_metric", "cosine"),
        }
    )
    vector_store.upsert(
        [
            ChunkRecord.from_chunk(chunk, dense_vector=vector, sparse_vector=sparse)
            for chunk, vector, sparse in zip(chunks, vectors, sparse_vectors, strict=True)
        ]
    )
    bm25 = BM25Indexer(root / "bm25")
    bm25.build(chunks, sparse_vectors)

    engines = {
        "bm25": _engine(settings, embedding, vector_store, bm25, dense=False, sparse=True),
        "dense": _engine(settings, embedding, vector_store, bm25, dense=True, sparse=False),
        "hybrid": _engine(settings, embedding, vector_store, bm25, dense=True, sparse=True),
    }
    text_cases = [case for case in cases if case.kind == "text"]
    strategies = {name: _evaluate(engine, text_cases) for name, engine in engines.items()}
    selected_name = _selected_strategy(settings)
    selected = strategies[selected_name]
    image = _evaluate(engines[selected_name], [case for case in cases if case.kind == "image"])
    passed = selected["hit_at_5"] >= MIN_HIT_AT_5 and selected["mrr_at_5"] >= MIN_MRR_AT_5
    if image["case_count"]:
        passed = passed and image["hit_at_5"] >= MIN_IMAGE_HIT_AT_5
    return {
        "dataset": dataset,
        "embedding": {
            "provider": settings.embedding.get("provider"),
            "model": settings.embedding.get("model"),
            "dimension": len(vectors[0]) if vectors else 0,
        },
        "chunk_count": len(chunks),
        "strategies": strategies,
        "image_cases": image,
        "gate": {
            "strategy": selected_name,
            "min_hit_at_5": MIN_HIT_AT_5,
            "min_mrr_at_5": MIN_MRR_AT_5,
            "min_image_hit_at_5": MIN_IMAGE_HIT_AT_5,
        },
        "passed": passed,
    }


def _selected_strategy(settings: Any) -> str:
    dense = settings.retrieval.get("enable_dense") is True
    sparse = settings.retrieval.get("enable_sparse") is True
    if dense and sparse:
        return "hybrid"
    if dense:
        return "dense"
    if sparse:
        return "bm25"
    raise ValueError("retrieval evaluation requires dense or sparse retrieval")


def _engine(
    settings: Any,
    embedding: Any,
    vector_store: ChromaStore,
    bm25: BM25Indexer,
    *,
    dense: bool,
    sparse: bool,
) -> HybridQueryEngine:
    retrieval = settings.retrieval
    return HybridQueryEngine(
        query_processor=QueryProcessor(),
        dense_retriever=DenseRetriever(embedding, vector_store) if dense else None,
        sparse_retriever=SparseRetriever(bm25) if sparse else None,
        fusion=RRFFusion(),
        metadata_filter=ExactMetadataFilter(),
        reranker=NoneReranker(),
        config=HybridSearchConfig(
            dense_top_k=int(retrieval.get("top_k_dense", 20)),
            sparse_top_k=int(retrieval.get("top_k_sparse", 20)),
            fusion_top_k=TOP_K,
            enable_dense=dense,
            enable_sparse=sparse,
        ),
    )


def _evaluate(engine: HybridQueryEngine, cases: list[_Case]) -> dict[str, Any]:
    if not cases:
        return {"case_count": 0, "hit_at_5": 0.0, "mrr_at_5": 0.0, "misses": []}
    hits = 0
    reciprocal_ranks = 0.0
    misses: list[str] = []
    for case in cases:
        request = QueryRequest(query=case.query, top_k=TOP_K, collection="evaluation")
        results = engine.search(request)
        first_rank = _first_relevant_rank(
            results,
            case.expected_sections,
            metadata_key=case.relevant_metadata_key,
        )
        if first_rank is not None and case.kind == "image":
            relevant = results[first_rank - 1]
            response = ResponseBuilder().build(request, results)
            if not any(image.source_ref == relevant.chunk_id for image in response.images):
                first_rank = None
        if first_rank is None:
            misses.append(case.query)
            continue
        hits += 1
        reciprocal_ranks += 1.0 / first_rank
    return {
        "case_count": len(cases),
        "hit_at_5": hits / len(cases),
        "mrr_at_5": reciprocal_ranks / len(cases),
        "misses": misses,
    }


def _first_relevant_rank(
    results: list[RetrievalCandidate],
    expected_sections: frozenset[str],
    *,
    metadata_key: str = "section_id",
) -> int | None:
    return next(
        (
            rank
            for rank, result in enumerate(results, 1)
            if result.metadata.get(metadata_key) in expected_sections
        ),
        None,
    )


def _load_dataset(settings: Any, dataset: str) -> tuple[list[Chunk], list[_Case]]:
    if dataset == "postgres":
        return _load_postgres_dataset(settings)
    if dataset == "hotpotqa":
        return _load_hotpotqa_dataset(
            settings,
            DEFAULT_HOTPOT_DIR / "corpus.jsonl",
            DEFAULT_HOTPOT_DIR / "queries.jsonl",
        )
    raise ValueError(f"unsupported retrieval dataset: {dataset}")


def _load_postgres_dataset(settings: Any) -> tuple[list[Chunk], list[_Case]]:
    chunks = _load_chunked_corpus(settings, DEFAULT_CORPUS, source_extension=".html")
    cases = [
        _Case(
            query=str(row["query"]),
            expected_sections=frozenset(str(item) for item in row["expected_section_ids"]),
        )
        for row in _read_jsonl(DEFAULT_GOLDEN)
    ]
    return chunks, cases


def _load_hotpotqa_dataset(
    settings: Any,
    corpus_path: Path,
    queries_path: Path,
) -> tuple[list[Chunk], list[_Case]]:
    if not corpus_path.is_file() or not queries_path.is_file():
        raise ValueError(
            "HotpotQA benchmark is missing; run `python scripts/prepare_hotpotqa.py` first"
        )
    chunks = _load_chunked_corpus(settings, corpus_path, source_extension=".json")
    cases: list[_Case] = []
    for row in _read_jsonl(queries_path):
        query = row.get("query")
        titles = row.get("expected_titles")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("HotpotQA query records require a non-empty query")
        if (
            not isinstance(titles, list)
            or not titles
            or not all(isinstance(title, str) and title for title in titles)
        ):
            raise ValueError("HotpotQA query records require non-empty expected_titles")
        cases.append(
            _Case(
                query=query,
                expected_sections=frozenset(titles),
                relevant_metadata_key="title",
            )
        )
    return chunks, cases


def _load_chunked_corpus(
    settings: Any,
    corpus_path: Path,
    *,
    source_extension: str,
) -> list[Chunk]:
    chunk_records = list(
        build_chunks(_read_jsonl(corpus_path), splitter_factory=lambda: create_splitter(settings))
    )
    return [
        Chunk(
            id=record.chunk_id,
            text=record.text,
            metadata={
                "section_id": record.section_id,
                "section": record.section_id,
                "title": record.title,
                "collection": "evaluation",
                "source_path": f"{record.section_id}{source_extension}",
            },
            source_ref=record.section_id,
            chunk_index=index,
        )
        for index, record in enumerate(chunk_records)
    ]


def _build_image_dataset(settings: Any, root: Path) -> tuple[list[Chunk], list[_Case]]:
    raw_chunks: list[Chunk] = []
    cases: list[_Case] = []
    for index, (section_id, query, lines) in enumerate(_IMAGE_CASES):
        image_id = f"image-{index + 1}"
        path = _draw_image(root / f"{image_id}.png", lines)
        raw_chunks.append(
            Chunk(
                id=section_id,
                text=f"[IMAGE: {image_id}]",
                metadata={
                    "section_id": section_id,
                    "section": section_id,
                    "title": lines[0],
                    "collection": "evaluation",
                    "source_path": f"{section_id}.pdf",
                    "image_refs": [image_id],
                    "images": [{"id": image_id, "path": str(path), "mime_type": "image/png"}],
                },
                source_ref=section_id,
                chunk_index=0,
            )
        )
        cases.append(_Case(query=query, expected_sections=frozenset({section_id}), kind="image"))

    chunks = ImageCaptioner(settings).transform(raw_chunks)
    failed = [chunk.id for chunk in chunks if chunk.metadata.get("has_unprocessed_images")]
    if failed:
        raise RuntimeError(f"image captioning failed for: {', '.join(failed)}")
    return chunks, cases


def _draw_image(path: Path, lines: tuple[str, ...]) -> Path:
    with pymupdf.open() as document:
        page = document.new_page(width=900, height=260)
        for index, line in enumerate(lines):
            page.insert_text((50, 65 + index * 70), line, fontsize=28)
        page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(path)
    return path


def _embed_batches(embedding: Any, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedding.embed(texts[start : start + batch_size]))
    return vectors


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    raise SystemExit(main())
