"""Provider-neutral ingestion pipeline orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from src.core.trace import TraceContext
from src.core.types import (
    Chunk,
    ChunkRecord,
    ImageRef,
    IngestionRequest,
    IngestionResult,
    JsonDict,
)
from src.ports.ingestion import (
    BM25IndexStore,
    BaseEmbedding,
    BaseLoader,
    BaseTransform,
    BaseVectorStore,
    DocumentChunker,
    FileIntegrityStore,
    ImageStore,
    SparseEncoder,
)


class IngestionPipeline:
    """Composable ingestion pipeline.

    This keeps the useful design from ``main``: load -> split -> transform ->
    encode -> upsert, but removes provider construction and global settings from
    the pipeline itself.
    """

    _TOTAL_STAGES = 7

    def __init__(
        self,
        integrity: FileIntegrityStore,
        loader: BaseLoader,
        chunker: DocumentChunker,
        transforms: list[BaseTransform],
        embedding: BaseEmbedding,
        sparse_encoder: SparseEncoder,
        vector_store: BaseVectorStore,
        bm25_store: BM25IndexStore,
        image_store: ImageStore,
    ) -> None:
        self.integrity = integrity
        self.loader = loader
        self.chunker = chunker
        self.transforms = list(transforms)
        self.embedding = embedding
        self.sparse_encoder = sparse_encoder
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.image_store = image_store

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        return self.run(request, on_progress=on_progress, trace=trace)

    def run(
        self,
        request: IngestionRequest,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        file_hash = ""

        try:
            self._notify("integrity", 1, on_progress, trace)
            file_hash = self.integrity.compute_sha256(request.source_path)
            if not request.force and self.integrity.should_skip(file_hash, request.collection):
                result = IngestionResult(
                    source_path=request.source_path,
                    collection=request.collection,
                    status="skipped",
                    file_hash=file_hash,
                    metadata={"reason": "file already ingested"},
                )
                self._record(trace, "skipped", result.to_dict())
                return result

            self.integrity.mark_processing(file_hash, request.source_path, request.collection)

            self._notify("load", 2, on_progress, trace)
            document = self.loader.load(request.source_path, request.collection, trace=trace)

            self._notify("split", 3, on_progress, trace)
            chunks = self.chunker.split_document(document, trace=trace)

            self._notify("transform", 4, on_progress, trace)
            for transform in self.transforms:
                chunks = transform.transform(chunks, trace=trace)

            self._notify("encode", 5, on_progress, trace)
            dense_vectors = self.embedding.embed([chunk.text for chunk in chunks], trace=trace) if chunks else []
            sparse_vectors = self.sparse_encoder.encode(chunks, trace=trace) if chunks else []
            self._validate_vector_counts(chunks, dense_vectors, sparse_vectors)
            records = self._build_records(chunks, dense_vectors, sparse_vectors)

            self._notify("upsert", 6, on_progress, trace)
            self.vector_store.upsert(records, trace=trace)
            self.bm25_store.upsert(chunks, sparse_vectors, trace=trace)
            images = _coerce_image_refs(
                document.metadata.get("images", []),
                collection=request.collection,
                source_path=request.source_path,
            )
            self.image_store.save_refs(images, trace=trace)

            self.integrity.mark_success(
                file_hash,
                request.source_path,
                request.collection,
                len(chunks),
            )
            self._notify("complete", 7, on_progress, trace)
            result = IngestionResult(
                source_path=request.source_path,
                collection=request.collection,
                status="success",
                file_hash=file_hash,
                document_id=document.id,
                chunk_count=len(chunks),
                image_count=len(images),
            )
            self._record(trace, "result", result.to_dict())
            return result
        except Exception as exc:
            if file_hash:
                self.integrity.mark_failed(file_hash, request.source_path, request.collection, str(exc))
            result = IngestionResult(
                source_path=request.source_path,
                collection=request.collection,
                status="failed",
                file_hash=file_hash,
                error=str(exc),
            )
            self._record(trace, "failed", result.to_dict())
            return result

    def _notify(
        self,
        stage: str,
        step: int,
        on_progress: Callable[[str, int, int], None] | None,
        trace: TraceContext | None,
    ) -> None:
        if on_progress is not None:
            on_progress(stage, step, self._TOTAL_STAGES)
        self._record(trace, stage, {"step": step, "total": self._TOTAL_STAGES})

    @staticmethod
    def _record(trace: TraceContext | None, stage: str, data: JsonDict) -> None:
        if trace is not None:
            trace.record_stage(stage, data)

    @staticmethod
    def _validate_vector_counts(
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[JsonDict],
    ) -> None:
        if len(dense_vectors) != len(chunks):
            raise ValueError("embedding output count must match chunk count")
        if len(sparse_vectors) != len(chunks):
            raise ValueError("sparse output count must match chunk count")

    @staticmethod
    def _build_records(
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[JsonDict],
    ) -> list[ChunkRecord]:
        return [
            ChunkRecord.from_chunk(
                chunk,
                dense_vector=dense_vectors[index],
                sparse_vector=sparse_vectors[index],
                content_hash=_content_hash(chunk.text),
            )
            for index, chunk in enumerate(chunks)
        ]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_image_refs(
    raw_images: object,
    collection: str,
    source_path: str,
) -> list[ImageRef]:
    if not isinstance(raw_images, list):
        return []

    refs: list[ImageRef] = []
    for raw in raw_images:
        if isinstance(raw, ImageRef):
            refs.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        image_id = payload.get("image_id", payload.get("id"))
        if not image_id:
            continue
        refs.append(
            ImageRef(
                image_id=str(image_id),
                path=str(payload.get("path", "")),
                collection=str(payload.get("collection", collection)),
                source_path=str(payload.get("source_path", source_path)),
                page=payload.get("page") if isinstance(payload.get("page"), int) else None,
                mime_type=str(payload.get("mime_type", "image/png")),
                text_offset=payload.get("text_offset")
                if isinstance(payload.get("text_offset"), int)
                else None,
                text_length=payload.get("text_length")
                if isinstance(payload.get("text_length"), int)
                else None,
                position=payload.get("position") if isinstance(payload.get("position"), dict) else {},
            )
        )
    return refs
