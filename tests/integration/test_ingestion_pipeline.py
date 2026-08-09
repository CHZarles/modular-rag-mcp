from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from core.query_engine import DenseRetriever
from core.trace import TraceContext
from core.types import Document, IngestionRequest
from ingestion.chunking import DocumentChunker
from ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
from ingestion.pipeline import IngestionPipeline
from ingestion.storage import BM25Indexer, ImageStorage
from libs.loader import SQLiteIntegrityStore
from libs.vector_store import ChromaStore

pytestmark = pytest.mark.integration


class ParagraphSplitter:
    """用段落边界生成稳定测试块，把测试重点留在 Pipeline 编排。"""

    def split_text(self, text: str, trace: object | None = None) -> list[str]:
        return [paragraph for paragraph in text.split("\n\n") if paragraph]


class DeterministicEmbedding:
    """无需外部 API 的确定性稠密向量实现。"""

    def embed(self, texts: list[str], trace: object | None = None) -> list[list[float]]:
        return [
            [float(len(text)), float(sum(text.encode("utf-8")) % 997 + 1)] for text in texts
        ]


class BlockingBatchProcessor:
    """让测试能在 A 已领取、尚未写索引时安排 B 完成接管。"""

    def __init__(self, delegate: BatchProcessor) -> None:
        self.delegate = delegate
        self.started = Event()
        self.release = Event()

    def process(
        self,
        chunks: list,
        trace: object | None = None,
    ) -> tuple[list[list[float]], list[dict]]:
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test did not release blocked ingestion")
        return self.delegate.process(chunks, trace=trace)


class FixtureLoader:
    supported_extensions = (".pdf",)

    def __init__(self, image_root: Path, *, fail: bool = False) -> None:
        self.image_root = image_root
        self.fail = fail

    def load(
        self,
        source_path: str,
        collection: str,
        trace: object | None = None,
    ) -> Document:
        if self.fail:
            raise ValueError("parse exploded")

        source = Path(source_path)
        source_text = source.read_text(encoding="utf-8")
        image_id = hashlib.sha256(source.read_bytes()).hexdigest()
        image_path = self.image_root / collection / f"{image_id}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"pipeline-image")
        return Document(
            id=f"doc-{image_id[:12]}",
            text=f"{source_text}\n\n[IMAGE: {image_id}]",
            metadata={
                "source_path": source_path,
                "collection": collection,
                "doc_type": "pdf",
                "images": [
                    {
                        "image_id": image_id,
                        "path": str(image_path),
                        "page": 1,
                        "mime_type": "image/png",
                    }
                ],
            },
        )


class SourceMutatingLoader(FixtureLoader):
    """在 Loader 读取前修改原文件，用来证明 Loader 实际读取的是快照。"""

    def __init__(self, image_root: Path, original_source: Path) -> None:
        super().__init__(image_root)
        self.original_source = original_source

    def load(
        self,
        source_path: str,
        collection: str,
        trace: object | None = None,
    ) -> Document:
        self.original_source.write_text("changedafterclaim content", encoding="utf-8")
        return super().load(source_path, collection, trace=trace)


def build_pipeline(
    tmp_path: Path,
    *,
    fail_loader: bool = False,
) -> tuple[IngestionPipeline, SQLiteIntegrityStore, ChromaStore, BM25Indexer, ImageStorage]:
    image_root = tmp_path / "images"
    integrity = SQLiteIntegrityStore(tmp_path / "ingestion_history.db")
    vector_store = ChromaStore(
        {
            "persist_path": str(tmp_path / "chroma"),
            "collection_name": "pipeline",
            "distance_metric": "cosine",
        }
    )
    # 查询组件必须和 Pipeline 共用同一份 active_generation 控制面。
    bm25_store = BM25Indexer(tmp_path / "bm25", generation_store=integrity)
    image_store = ImageStorage(
        tmp_path / "image_index.db",
        image_root,
        generation_store=integrity,
    )
    batch_processor = BatchProcessor(
        DenseEncoder(embedding=DeterministicEmbedding()),
        SparseEncoder(),
        batch_size=1,
    )
    pipeline = IngestionPipeline(
        integrity=integrity,
        loader=FixtureLoader(image_root, fail=fail_loader),
        chunker=DocumentChunker(splitter=ParagraphSplitter()),
        transforms=[],
        batch_processor=batch_processor,
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        claim_lease_seconds=60,
    )
    return pipeline, integrity, vector_store, bm25_store, image_store


def test_pipeline_persists_dense_sparse_and_image_outputs_with_shared_ids(tmp_path: Path) -> None:
    source = tmp_path / "simple.pdf"
    source.write_text("Pipeline orchestration writes searchable storage.", encoding="utf-8")
    pipeline, integrity, vector_store, bm25_store, image_store = build_pipeline(tmp_path)
    progress: list[tuple[str, int, int]] = []
    trace = TraceContext(trace_type="ingestion")

    result = pipeline.run(
        IngestionRequest(source_path=str(source), collection="docs"),
        on_progress=lambda stage, step, total: progress.append((stage, step, total)),
        trace=trace,
    )
    trace.finish()

    assert result.status == "success"
    assert result.chunk_count == 2
    assert result.image_count == 1
    assert [stage for stage, _, _ in progress] == [
        "integrity",
        "load",
        "split",
        "transform",
        "encode",
        "store",
        "complete",
    ]

    sparse_hits = bm25_store.query(["pipeline"], top_k=5)
    sparse_ids = [hit.id for hit in sparse_hits]
    dense_records = vector_store.get_by_ids(sparse_ids)
    assert sparse_ids
    assert [record.id for record in dense_records] == sparse_ids
    assert all(record.sparse_vector is not None for record in dense_records)
    assert vector_store.persist_path.joinpath("chroma.sqlite3").is_file()
    assert bm25_store.index_path.is_file()

    images = image_store.list_by_document(str(source), "docs")
    assert len(images) == 1
    assert Path(images[0].path).is_file()
    assert integrity.list_processed("docs")[0]["status"] == "success"

    required_stages = {"load", "split", "transform", "embed", "upsert"}
    stages = {stage["stage"]: stage for stage in trace.stages}
    assert required_stages <= stages.keys()
    assert trace.to_dict()["trace_type"] == "ingestion"
    for stage_name in required_stages:
        matching = [stage for stage in trace.stages if stage["stage"] == stage_name]
        assert len(matching) == 1
        stage = matching[0]
        assert isinstance(stage["elapsed_ms"], float)
        assert stage["elapsed_ms"] >= 0
        assert isinstance(stage["data"]["method"], str)
        assert stage["data"]["method"]
        assert isinstance(stage["data"]["provider"], str)
        assert isinstance(stage["data"]["details"], dict)

    assert stages["load"]["data"]["details"]["image_count"] == 1
    assert stages["split"]["data"]["details"]["output_count"] == 2
    assert stages["transform"]["data"]["details"]["transformer_count"] == 0
    assert stages["embed"]["data"]["details"]["dense_vector_count"] == 2
    assert stages["upsert"]["data"]["details"]["image_count"] == 1

    skipped = pipeline.run(IngestionRequest(source_path=str(source), collection="docs"))
    assert skipped.status == "skipped"
    assert skipped.metadata["reason"] == "already_succeeded"


def test_pipeline_replaces_previous_dense_and_sparse_document_entries(tmp_path: Path) -> None:
    source = tmp_path / "changing.pdf"
    source.write_text("legacykeyword content", encoding="utf-8")
    pipeline, _, vector_store, bm25_store, _ = build_pipeline(tmp_path)

    assert pipeline.run(IngestionRequest(str(source), "docs")).status == "success"
    old_ids = [hit.id for hit in bm25_store.query(["legacykeyword"], top_k=5)]
    assert old_ids

    source.write_text("replacementkeyword content", encoding="utf-8")
    assert pipeline.run(IngestionRequest(str(source), "docs")).status == "success"
    new_ids = [hit.id for hit in bm25_store.query(["replacementkeyword"], top_k=5)]

    assert bm25_store.query(["legacykeyword"], top_k=5) == []
    assert vector_store.get_by_ids(old_ids) == []
    assert new_ids
    assert [record.id for record in vector_store.get_by_ids(new_ids)] == new_ids


def test_pipeline_records_clear_failed_stage(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_text("broken fixture", encoding="utf-8")
    pipeline, integrity, _, _, _ = build_pipeline(tmp_path, fail_loader=True)
    trace = TraceContext(trace_type="ingestion")

    result = pipeline.run(
        IngestionRequest(source_path=str(source), collection="docs"),
        trace=trace,
    )

    assert result.status == "failed"
    assert result.error == "load stage failed: parse exploded"
    record = integrity.list_processed("docs")[0]
    assert record["status"] == "failed"
    assert record["error_msg"] == result.error
    load_stage = next(stage for stage in trace.stages if stage["stage"] == "load")
    assert load_stage["data"]["details"]["status"] == "error"
    assert load_stage["data"]["details"]["error"] == "parse exploded"
    assert load_stage["elapsed_ms"] >= 0


def test_failed_update_keeps_previous_generation_queryable(tmp_path: Path) -> None:
    source = tmp_path / "recoverable.pdf"
    source.write_text("stablekeyword content", encoding="utf-8")
    pipeline, integrity, vector_store, bm25_store, image_store = build_pipeline(tmp_path)
    assert pipeline.run(IngestionRequest(str(source), "docs")).status == "success"

    source.write_text("broken new content", encoding="utf-8")
    failing_pipeline = IngestionPipeline(
        integrity=integrity,
        loader=FixtureLoader(tmp_path / "images", fail=True),
        chunker=DocumentChunker(splitter=ParagraphSplitter()),
        transforms=[],
        batch_processor=pipeline.batch_processor,
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        claim_lease_seconds=60,
    )

    failed = failing_pipeline.run(IngestionRequest(str(source), "docs"))

    assert failed.status == "failed"
    hits = bm25_store.query(["stablekeyword"], top_k=5, filters={"collection": "docs"})
    assert hits
    assert all(hit.metadata["generation"] == 1 for hit in hits)


def test_takeover_fences_late_worker_across_all_storage_outputs(tmp_path: Path) -> None:
    source = tmp_path / "concurrent.pdf"
    source.write_text("baselinekeyword content", encoding="utf-8")
    pipeline_a, integrity, vector_store, bm25_store, image_store = build_pipeline(tmp_path)
    first = pipeline_a.run(IngestionRequest(str(source), "docs"))
    assert first.status == "success"

    # A 领取 generation=2 后停在编码阶段；测试随后让租约过期，由 B 领取 generation=3。
    source.write_text("staleworkerkeyword content", encoding="utf-8")
    blocking_processor = BlockingBatchProcessor(pipeline_a.batch_processor)
    pipeline_a.batch_processor = blocking_processor  # type: ignore[assignment]
    pipeline_b, _, _, _, _ = build_pipeline(tmp_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        late_future = pool.submit(
            pipeline_a.run,
            IngestionRequest(str(source), "docs", request_id="worker-a"),
        )
        assert blocking_processor.started.wait(timeout=5)
        with sqlite3.connect(integrity.db_path) as connection:
            connection.execute(
                "UPDATE document_state SET lease_expires_at = 0 WHERE doc_key = ?",
                (first.metadata["doc_key"],),
            )

        source.write_text("winningworkerkeyword content", encoding="utf-8")
        winner = pipeline_b.run(
            IngestionRequest(str(source), "docs", request_id="worker-b")
        )
        blocking_processor.release.set()
        late = late_future.result(timeout=10)

    assert winner.status == "success"
    assert winner.metadata["generation"] == 3
    assert late.status == "failed"
    assert "claim is no longer current" in str(late.metadata["status_record_error"])

    # A 在 B 发布后才写完 generation=2，因此同步 GC 来不及清掉它；物理垃圾仍存在。
    query_vector = DeterministicEmbedding().embed(["staleworkerkeyword"])[0]
    stale_records = vector_store.query(
        query_vector,
        top_k=10,
        filters={"doc_key": first.metadata["doc_key"], "generation": 2},
    )
    assert stale_records

    # 但 Dense、BM25 和图片查询都以 active_generation=3 为准，旧 worker 永远不可见。
    dense = DenseRetriever(
        DeterministicEmbedding(),
        vector_store,
        generation_store=integrity,
        overfetch_factor=10,
    )
    dense_hits = dense.retrieve(
        "staleworkerkeyword",
        top_k=10,
        filters={"collection": "docs"},
    )
    assert dense_hits
    assert all(candidate.metadata["generation"] == 3 for candidate in dense_hits)
    assert bm25_store.query(
        ["staleworkerkeyword"],
        top_k=10,
        filters={"collection": "docs"},
    ) == []
    assert bm25_store.query(
        ["winningworkerkeyword"],
        top_k=10,
        filters={"collection": "docs"},
    )
    images = image_store.list_by_document(str(source), "docs")
    assert images
    winning_hash = hashlib.sha256(b"winningworkerkeyword content").hexdigest()
    assert all(image.image_id.startswith(winning_hash) for image in images)


def test_completion_callback_failure_does_not_undo_published_generation(tmp_path: Path) -> None:
    source = tmp_path / "callback.pdf"
    source.write_text("callbackkeyword content", encoding="utf-8")
    pipeline, integrity, _, bm25_store, _ = build_pipeline(tmp_path)

    def progress(stage: str, step: int, total: int) -> None:
        if stage == "complete":
            raise RuntimeError("client disconnected")

    result = pipeline.run(
        IngestionRequest(str(source), "docs"),
        on_progress=progress,
    )

    assert result.status == "success"
    assert integrity.get_active_generations("docs") == {
        result.metadata["doc_key"]: result.metadata["generation"]
    }
    assert bm25_store.query(["callbackkeyword"], top_k=5)


def test_loader_reads_same_immutable_snapshot_used_for_source_revision(tmp_path: Path) -> None:
    source = tmp_path / "mutable.pdf"
    original_bytes = b"snapshotkeyword content"
    source.write_bytes(original_bytes)
    pipeline, _, _, bm25_store, _ = build_pipeline(tmp_path)
    pipeline.loader = SourceMutatingLoader(tmp_path / "images", source)

    result = pipeline.run(IngestionRequest(str(source), "docs"))

    assert result.status == "success"
    assert result.file_hash == hashlib.sha256(original_bytes).hexdigest()
    hits = bm25_store.query(["snapshotkeyword"], top_k=5)
    assert hits
    assert all(hit.metadata["source_path"] == str(source) for hit in hits)
    assert bm25_store.query(["changedafterclaim"], top_k=5) == []


def test_loader_revision_change_creates_a_new_generation(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("same source bytes", encoding="utf-8")
    pipeline, integrity, _, _, _ = build_pipeline(tmp_path)
    pipeline.loader.revision = "fixture:v1"

    first = pipeline.run(IngestionRequest(str(source), "docs"))
    repeated = pipeline.run(IngestionRequest(str(source), "docs"))
    pipeline.loader.revision = "fixture:v2"
    changed = pipeline.run(IngestionRequest(str(source), "docs"))

    assert first.status == "success"
    assert repeated.status == "skipped"
    assert changed.status == "success"
    assert first.file_hash == changed.file_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    processed = integrity.list_processed("docs")
    assert processed[0]["generation"] == 2
