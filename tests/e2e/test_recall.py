"""Golden-set retrieval recall regression over the real local pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from scripts.ingest import main as ingest_main
from src.core.services import build_local_query_engine
from src.core.settings import load_settings
from src.libs.embedding import EmbeddingFactory
from src.libs.evaluator import CustomEvaluator
from src.observability.evaluation import EvalRunner

pytestmark = pytest.mark.e2e
MIN_SOURCE_HIT_RATE = 0.8


class RecallEmbedding:
    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        return [[float(len(text)), float(sum(text.encode("utf-8")) % 997 + 1)] for text in texts]


def test_golden_set_source_recall_stays_above_threshold(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    _write_pdf(
        documents / "generation-lifecycle.pdf",
        "The ingestion generation lifecycle uses an active generation pointer. "
        "Stale data remains invisible until a new generation is published.",
    )
    _write_pdf(
        documents / "hybrid-retrieval.pdf",
        "Dense and sparse retrieval results are combined with reciprocal rank fusion. "
        "RRF merges semantic vector search with BM25 keyword search.",
    )
    settings_path = _write_settings(tmp_path)
    EmbeddingFactory.register("recall-deterministic", lambda config: RecallEmbedding())

    assert (
        ingest_main(
            ["--path", str(documents), "--collection", "default"],
            settings_path=settings_path,
        )
        == 0
    )
    settings = load_settings(str(settings_path))
    runner = EvalRunner(settings, build_local_query_engine(settings), CustomEvaluator())
    golden_path = Path(__file__).resolve().parents[1] / "fixtures" / "golden_test_set.json"

    report = runner.run(golden_path)

    assert report.metadata["case_count"] == 2
    assert report.metrics["source_hit_rate"] >= MIN_SOURCE_HIT_RATE


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _write_settings(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    content = f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
embedding:
  provider: recall-deterministic
splitter:
  provider: recursive
  chunk_size: 300
  chunk_overlap: 30
ingestion:
  claim_lease_seconds: 60
  batch_size: 2
  storage:
    integrity_db_path: {data / "db/integrity.db"}
    bm25_path: {data / "db/bm25"}
    image_db_path: {data / "db/images.db"}
    image_root: {data / "images"}
  chunk_refiner:
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    enabled: false
vector_store:
  backend: chroma
  persist_path: {data / "db/chroma"}
  collection_name: recall
  distance_metric: cosine
retrieval:
  sparse_backend: bm25
  fusion_algorithm: rrf
  top_k_dense: 5
  top_k_sparse: 5
  top_k_final: 3
rerank:
  backend: none
  top_m: 5
  timeout_seconds: 5
evaluation:
  backends: [custom]
  golden_test_set: tests/fixtures/golden_test_set.json
observability:
  enabled: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path
