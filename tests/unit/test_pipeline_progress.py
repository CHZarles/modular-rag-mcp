from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call

from core.types import Chunk, ClaimHandle, ClaimResult, Document, IngestionRequest
from ingestion.pipeline import IngestionPipeline


def test_pipeline_reports_every_stage_in_order(tmp_path: Path) -> None:
    pipeline = _build_pipeline()
    source = tmp_path / "document.pdf"
    source.write_text("progress fixture", encoding="utf-8")
    on_progress = Mock()

    result = pipeline.run(
        IngestionRequest(source_path=str(source), collection="docs"),
        on_progress=on_progress,
    )

    assert result.status == "success"
    assert on_progress.call_args_list == [
        call("integrity", 1, 7),
        call("load", 2, 7),
        call("split", 3, 7),
        call("transform", 4, 7),
        call("encode", 5, 7),
        call("store", 6, 7),
        call("complete", 7, 7),
    ]


def test_pipeline_runs_normally_without_progress_callback(tmp_path: Path) -> None:
    pipeline = _build_pipeline()
    source = tmp_path / "document.pdf"
    source.write_text("progress fixture", encoding="utf-8")

    result = pipeline.run(IngestionRequest(source_path=str(source), collection="docs"))

    assert result.status == "success"
    pipeline.integrity.publish.assert_called_once()


def test_pipeline_renews_claim_while_a_slow_stage_is_running(tmp_path: Path) -> None:
    class SlowTransform:
        name = "slow"

        def transform(self, chunks: list[Chunk], trace: object | None = None) -> list[Chunk]:
            time.sleep(0.09)
            return chunks

    pipeline = _build_pipeline(
        transforms=[SlowTransform()],  # type: ignore[list-item]
        claim_lease_seconds=0.06,
    )
    pipeline.integrity.renew_lease.side_effect = lambda claim, seconds: replace(
        claim,
        lease_expires_at=time.time() + seconds,
    )
    source = tmp_path / "slow.pdf"
    source.write_text("slow fixture", encoding="utf-8")

    result = pipeline.run(IngestionRequest(source_path=str(source), collection="docs"))

    assert result.status == "success"
    assert pipeline.integrity.renew_lease.call_count >= 2


def _build_pipeline(
    *,
    transforms: list[object] | None = None,
    claim_lease_seconds: float = 900,
) -> IngestionPipeline:
    claim = ClaimHandle(
        doc_key="doc-key",
        generation=1,
        source_revision="revision",
        claim_token="claim-token",
        lease_owner="worker",
        lease_expires_at=1.0,
    )
    integrity = Mock()
    integrity.compute_doc_key.return_value = claim.doc_key
    integrity.compute_sha256.return_value = claim.source_revision
    integrity.try_claim.return_value = ClaimResult(status="acquired", handle=claim)
    integrity.list_garbage_generations.return_value = []

    loader = Mock()
    loader.load.return_value = Document(id="temporary", text="document text", metadata={})
    chunker = Mock()
    chunker.split_document.return_value = [
        Chunk(
            id="chunk-0",
            text="document text",
            metadata={},
            source_ref="temporary",
            chunk_index=0,
        )
    ]
    batch_processor = Mock()
    batch_processor.process.return_value = ([[0.1, 0.2]], [{"document": 1.0}])

    vector_store = Mock()
    bm25_store = Mock()
    image_store = Mock()
    return IngestionPipeline(
        integrity=integrity,
        loader=loader,
        chunker=chunker,
        transforms=transforms or [],  # type: ignore[arg-type]
        batch_processor=batch_processor,
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        claim_lease_seconds=claim_lease_seconds,
    )
