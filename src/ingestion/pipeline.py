"""与供应商无关的文档摄取流水线编排。"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from src.core.trace import TraceContext
from src.core.types import (
    Chunk,
    ClaimHandle,
    Document,
    ImageRef,
    IngestionRequest,
    IngestionResult,
    JsonDict,
    ProgressCallback,
)
from src.ingestion.embedding import BatchProcessor
from src.ingestion.storage import SQLiteGrepIndex, VectorUpserter
from src.observability.logger import get_logger
from src.ports.ingestion import (
    BaseLoader,
    BaseTransform,
    BaseVectorStore,
    BM25IndexStore,
    DocumentChunker,
    FileIntegrityStore,
    ImageStore,
)

logger = get_logger(__name__)


class IngestionPipeline:
    """可组合的文档摄取流水线。

    流程固定为加载、切分、转换、编码和写入，但供应商组件构造与全局配置不进入
    Pipeline 本身，所有外部能力均通过端口注入。
    """

    _PROGRESS_STAGES = (
        "integrity",
        "load",
        "split",
        "transform",
        "encode",
        "store",
        "complete",
    )
    _TOTAL_STAGES = len(_PROGRESS_STAGES)

    def __init__(
        self,
        integrity: FileIntegrityStore,
        loader: BaseLoader,
        chunker: DocumentChunker,
        transforms: list[BaseTransform],
        batch_processor: BatchProcessor,
        vector_store: BaseVectorStore,
        bm25_store: BM25IndexStore,
        image_store: ImageStore,
        grep_index: SQLiteGrepIndex | None = None,
        claim_lease_seconds: float = 900,
        enable_dense: bool = True,
        index_dimension_validator: Callable[[int], None] | None = None,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("ingestion pipeline configuration error: claim lease must be positive")
        self.integrity = integrity
        self.loader = loader
        self.chunker = chunker
        self.transforms = list(transforms)
        self.batch_processor = batch_processor
        self.vector_store = vector_store
        self.enable_dense = enable_dense
        self.vector_upserter = VectorUpserter(vector_store, enabled=enable_dense)
        self.bm25_store = bm25_store
        self.image_store = image_store
        self.grep_index = grep_index
        self.claim_lease_seconds = claim_lease_seconds
        self.index_dimension_validator = index_dimension_validator

    def ingest(
        self,
        request: IngestionRequest,
        on_progress: ProgressCallback | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        return self.run(request, on_progress=on_progress, trace=trace)

    def run(
        self,
        request: IngestionRequest,
        on_progress: ProgressCallback | None = None,
        trace: TraceContext | None = None,
    ) -> IngestionResult:
        pipeline_started = time.monotonic()
        file_hash = ""
        source_revision = ""
        normalized_source_path = ""
        doc_key = ""
        current_stage = "integrity"
        lease_owner = f"{request.request_id or 'ingestion'}:{uuid4().hex}"
        claim: ClaimHandle | None = None
        heartbeat: _LeaseHeartbeat | None = None

        try:
            self._notify("integrity", on_progress)
            normalized_source_path = str(
                Path(request.source_path).expanduser().resolve(strict=True)
            )
            doc_key = self.integrity.compute_doc_key(
                normalized_source_path,
                request.collection,
            )

            # 哈希和 Loader 必须读取同一份不可变副本。否则源文件在两次读取之间变化时，
            # 数据库记录的 source_revision 可能属于 V1，实际索引却来自 V2。
            with tempfile.TemporaryDirectory(prefix="rag-ingestion-") as temporary_dir:
                snapshot_path = _create_source_snapshot(
                    normalized_source_path,
                    Path(temporary_dir),
                )
                file_hash = self.integrity.compute_sha256(str(snapshot_path))
                source_revision = _ingestion_revision(
                    file_hash,
                    getattr(self.loader, "revision", ""),
                )
                claim_result = self.integrity.try_claim(
                    source_revision,
                    normalized_source_path,
                    request.collection,
                    lease_owner,
                    self.claim_lease_seconds,
                    force=request.force,
                )
                # 已发布相同内容和其他任务的有效租约都属于正常控制流，不是摄取失败。
                if claim_result.status != "acquired":
                    result = IngestionResult(
                        source_path=normalized_source_path,
                        collection=request.collection,
                        status="skipped",
                        file_hash=file_hash,
                        metadata={"reason": claim_result.status, "doc_key": doc_key},
                    )
                    _record_trace_stage(
                        trace,
                        "ingestion",
                        method="ingest",
                        provider=type(self).__name__,
                        details={"status": "skipped", "reason": claim_result.status},
                        elapsed_ms=_elapsed_ms(pipeline_started),
                    )
                    return result
                if claim_result.handle is None:  # ClaimResult 自身也校验此不变量。
                    raise RuntimeError("integrity store returned an acquired claim without handle")
                claim = claim_result.handle
                heartbeat = _LeaseHeartbeat(
                    self.integrity,
                    claim,
                    self.claim_lease_seconds,
                )
                heartbeat.start()

                current_stage = "load"
                self._notify("load", on_progress)
                with _trace_stage(
                    trace,
                    "load",
                    method="load",
                    provider=type(self.loader).__name__,
                ) as stage_details:
                    loaded = self.loader.load(str(snapshot_path), request.collection, trace=trace)
                    document = _restore_document_identity(
                        loaded,
                        document_id=file_hash,
                        source_revision=source_revision,
                        source_path=normalized_source_path,
                        collection=request.collection,
                    )
                    raw_images = document.metadata.get("images", [])
                    stage_details.update(
                        {
                            "document_id": document.id,
                            "text_length": len(document.text),
                            "image_count": len(raw_images) if isinstance(raw_images, list) else 0,
                        }
                    )

                current_stage = "split"
                self._notify("split", on_progress)
                with _trace_stage(
                    trace,
                    "split",
                    method="split",
                    provider=type(self.chunker).__name__,
                    details={"input_length": len(document.text)},
                ) as stage_details:
                    chunks = self.chunker.split_document(document, trace=trace)
                    stage_details.update(
                        {
                            "output_count": len(chunks),
                            "average_chunk_length": (
                                sum(len(chunk.text) for chunk in chunks) // len(chunks)
                                if chunks
                                else 0
                            ),
                        }
                    )

                current_stage = "transform"
                self._notify("transform", on_progress)
                transform_names = [transform.name for transform in self.transforms]
                with _trace_stage(
                    trace,
                    "transform",
                    method="sequential" if self.transforms else "none",
                    provider=type(self).__name__,
                    details={
                        "input_count": len(chunks),
                        "transformer_count": len(self.transforms),
                        "transformers": transform_names,
                    },
                ) as stage_details:
                    # 转换器按注册顺序串行执行，后一个转换器接收前一个的输出。
                    for transform in self.transforms:
                        stage_details["current_transform"] = transform.name
                        chunks = transform.transform(chunks, trace=trace)
                    stage_details.pop("current_transform", None)
                    chunks = _stamp_generation(
                        chunks,
                        claim,
                        normalized_source_path,
                        request.collection,
                    )
                    stage_details["output_count"] = len(chunks)

                current_stage = "encode"
                self._notify("encode", on_progress)
                with _trace_stage(
                    trace,
                    "embed",
                    method="batch",
                    provider=type(self.batch_processor).__name__,
                    details={"input_count": len(chunks)},
                ) as stage_details:
                    dense_vectors, sparse_vectors = self.batch_processor.process(
                        chunks, trace=trace
                    )
                    if dense_vectors and self.index_dimension_validator is not None:
                        self.index_dimension_validator(len(dense_vectors[0]))
                    stage_details.update(
                        {
                            "dense_vector_count": len(dense_vectors),
                            "dense_dimension": len(dense_vectors[0]) if dense_vectors else 0,
                            "sparse_vector_count": len(sparse_vectors),
                        }
                    )

                current_stage = "store"
                self._notify("store", on_progress)
                with _trace_stage(
                    trace,
                    "upsert",
                    method="generation_upsert",
                    provider=type(self.vector_store).__name__,
                    details={
                        "input_count": len(chunks),
                        "storage_providers": {
                            "vector": type(self.vector_store).__name__ if self.enable_dense else "disabled",
                            "sparse": type(self.bm25_store).__name__,
                            "grep": type(self.grep_index).__name__ if self.grep_index else "disabled",
                            "image": type(self.image_store).__name__,
                        },
                    },
                ) as stage_details:
                    # 新 generation 只能追加自己的不可见数据，不能先删除仍在对外服务的旧代。
                    records = self.vector_upserter.upsert(
                        chunks,
                        dense_vectors,
                        sparse_vectors,
                        trace=trace,
                    )
                    # 最终正文 ID 是 Dense/BM25 的共同身份；generation 已包含在该 ID 中。
                    indexed_chunks = [
                        replace(chunk, id=record.id)
                        for chunk, record in zip(chunks, records, strict=True)
                    ]
                    self.bm25_store.upsert(indexed_chunks, sparse_vectors, trace=trace)
                    if self.grep_index is not None:
                        self.grep_index.upsert(records)
                    images = _coerce_image_refs(
                        document.metadata.get("images", []),
                        collection=request.collection,
                        source_path=normalized_source_path,
                    )
                    self.image_store.save_refs(
                        images,
                        claim.doc_key,
                        claim.generation,
                        trace=trace,
                    )
                    stage_details.update(
                        {
                            "vector_count": len(records),
                            "sparse_count": len(sparse_vectors),
                            "image_count": len(images),
                        }
                    )

            # 快照已完成使命，先成功清理临时目录，再切换公开指针。这样即使临时目录清理
            # 异常，也只会让本代失败，不会出现“已经 Published 却返回 failed”。
            if claim is None:
                raise RuntimeError("ingestion pipeline lost its current claim handle")
            if heartbeat is None:
                raise RuntimeError("ingestion pipeline lost its lease heartbeat")
            claim = heartbeat.close()
            heartbeat = None
            # 所有外部存储都完成后先进入 staged，再用 generation + claim_token CAS 发布。
            # 旧 worker 即使写完自己的旧代，也会在这里被控制面拒绝。
            current_stage = "publish"
            self.integrity.mark_staged(claim)
            self.integrity.publish(claim, len(chunks))
            published_claim = claim
            claim = None
            result = IngestionResult(
                source_path=normalized_source_path,
                collection=request.collection,
                status="success",
                file_hash=file_hash,
                document_id=document.id,
                chunk_count=len(chunks),
                image_count=len(images),
                metadata={
                    "doc_key": published_claim.doc_key,
                    "generation": published_claim.generation,
                },
            )
            # 发布已经成功后，GC、进度回调或 Trace 失败都不能把业务结果改写成 failed。
            self._cleanup_garbage_generations(published_claim)
            self._notify_after_publish(on_progress)
            self._record_after_publish(trace, result, pipeline_started)
            return result
        except Exception as exc:
            if heartbeat is not None:
                claim = heartbeat.close(raise_on_error=False)
                heartbeat = None
            error = f"{current_stage} stage failed: {exc}"
            metadata: JsonDict = {
                key: value
                for key, value in (
                    ("doc_key", doc_key),
                    ("generation", claim.generation if claim else None),
                )
                if value not in ("", None)
            }
            # 只有真正领取到任务且 generation/claim_token 仍匹配的 worker 才能提交失败状态。
            marked_failed = False
            if claim is not None:
                try:
                    self.integrity.mark_failed(claim, error)
                    marked_failed = True
                except Exception as state_error:
                    metadata["status_record_error"] = str(state_error)
            if marked_failed and self.grep_index is not None and claim is not None:
                try:
                    self.grep_index.remove_generation(claim.doc_key, claim.generation)
                except Exception as cleanup_error:
                    logger.warning(
                        "Unable to delete failed grep generation %s/%s: %s",
                        claim.doc_key,
                        claim.generation,
                        cleanup_error,
                    )
            result = IngestionResult(
                source_path=normalized_source_path or request.source_path,
                collection=request.collection,
                status="failed",
                file_hash=file_hash,
                error=error,
                metadata=metadata,
            )
            _record_trace_stage(
                trace,
                "ingestion",
                method="ingest",
                provider=type(self).__name__,
                details={"status": "failed", "stage": current_stage, "error": error},
                elapsed_ms=_elapsed_ms(pipeline_started),
            )
            return result

    def _cleanup_garbage_generations(self, claim: ClaimHandle) -> None:
        """发布后尽力精确回收旧代；清理失败不回滚已经公开的新版本。"""
        try:
            generations = self.integrity.list_garbage_generations(claim.doc_key)
        except Exception as exc:
            logger.warning("Unable to list garbage generations for %s: %s", claim.doc_key, exc)
            return

        for generation in generations:
            try:
                filters = {"doc_key": claim.doc_key, "generation": generation}
                if self.enable_dense:
                    self.vector_store.delete_by_metadata(filters)
                self.bm25_store.remove_generation(claim.doc_key, generation)
                self.image_store.delete_generation(claim.doc_key, generation)
            except Exception as exc:
                logger.warning(
                    "Unable to delete garbage generation %s/%s: %s",
                    claim.doc_key,
                    generation,
                    exc,
                )
            if self.grep_index is not None:
                try:
                    self.grep_index.remove_generation(claim.doc_key, generation)
                except Exception as exc:
                    logger.warning(
                        "Unable to delete garbage grep generation %s/%s: %s",
                        claim.doc_key,
                        generation,
                        exc,
                    )

    def _notify_after_publish(
        self,
        on_progress: ProgressCallback | None,
    ) -> None:
        try:
            self._notify("complete", on_progress)
        except Exception as exc:
            logger.warning("Ingestion completion callback failed after publish: %s", exc)

    @staticmethod
    def _record_after_publish(
        trace: TraceContext | None,
        result: IngestionResult,
        pipeline_started: float,
    ) -> None:
        try:
            _record_trace_stage(
                trace,
                "ingestion",
                method="ingest",
                provider=IngestionPipeline.__name__,
                details={
                    "status": result.status,
                    "chunk_count": result.chunk_count,
                    "image_count": result.image_count,
                },
                elapsed_ms=_elapsed_ms(pipeline_started),
            )
        except Exception as exc:
            logger.warning("Ingestion result trace failed after publish: %s", exc)

    def _notify(
        self,
        stage: str,
        on_progress: ProgressCallback | None,
    ) -> None:
        if on_progress is not None:
            step = self._PROGRESS_STAGES.index(stage) + 1
            on_progress(stage, step, self._TOTAL_STAGES)


class _LeaseHeartbeat:
    """Renew one active claim while slow parsing, model, or storage calls are running."""

    def __init__(
        self,
        integrity: FileIntegrityStore,
        claim: ClaimHandle,
        lease_seconds: float,
    ) -> None:
        self._integrity = integrity
        self._claim = claim
        self._lease_seconds = lease_seconds
        self._interval = min(max(lease_seconds / 3.0, 0.01), 30.0)
        self._stop = Event()
        self._lock = Lock()
        self._error: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"lease-heartbeat-{claim.doc_key[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self, *, raise_on_error: bool = True) -> ClaimHandle:
        self._stop.set()
        self._thread.join()
        with self._lock:
            claim = self._claim
            error = self._error
        if error is not None and raise_on_error:
            raise RuntimeError(f"ingestion lease heartbeat failed: {error}") from error
        return claim

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._lock:
                    current = self._claim
                renewed = self._integrity.renew_lease(current, self._lease_seconds)
                with self._lock:
                    self._claim = renewed
            except Exception as exc:
                with self._lock:
                    self._error = exc
                self._stop.set()
                return


@contextmanager
def _trace_stage(
    trace: TraceContext | None,
    stage_name: str,
    *,
    method: str,
    provider: str,
    details: JsonDict | None = None,
) -> Iterator[JsonDict]:
    stage_details = dict(details or {})
    started = time.monotonic()
    try:
        yield stage_details
    except Exception as exc:
        stage_details.update(
            {
                "status": "error",
                "error": str(exc) or type(exc).__name__,
            }
        )
        _record_trace_stage(
            trace,
            stage_name,
            method=method,
            provider=provider,
            details=stage_details,
            elapsed_ms=_elapsed_ms(started),
        )
        raise
    else:
        stage_details.setdefault("status", "success")
        _record_trace_stage(
            trace,
            stage_name,
            method=method,
            provider=provider,
            details=stage_details,
            elapsed_ms=_elapsed_ms(started),
        )


def _record_trace_stage(
    trace: TraceContext | None,
    stage_name: str,
    *,
    method: str,
    provider: str,
    details: JsonDict,
    elapsed_ms: float,
) -> None:
    if trace is None:
        return
    trace.record_stage(
        stage_name,
        {"method": method, "provider": provider, "details": details},
        elapsed_ms=elapsed_ms,
    )


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _coerce_image_refs(
    raw_images: object,
    collection: str,
    source_path: str,
) -> list[ImageRef]:
    """把 Loader 输出的宽松图片字典规范化为领域对象。"""
    if not isinstance(raw_images, list):
        return []

    refs: list[ImageRef] = []
    for raw in raw_images:
        if isinstance(raw, ImageRef):
            refs.append(replace(raw, collection=collection, source_path=source_path))
            continue
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        image_id = payload.get("image_id", payload.get("id"))
        if not image_id:
            continue
        position = payload.get("position")
        refs.append(
            ImageRef(
                image_id=str(image_id),
                path=str(payload.get("path", "")),
                collection=collection,
                source_path=source_path,
                page=payload.get("page") if isinstance(payload.get("page"), int) else None,
                mime_type=str(payload.get("mime_type", "image/png")),
                text_offset=payload.get("text_offset")
                if isinstance(payload.get("text_offset"), int)
                else None,
                text_length=payload.get("text_length")
                if isinstance(payload.get("text_length"), int)
                else None,
                position=dict(position) if isinstance(position, dict) else {},
            )
        )
    return refs


def _create_source_snapshot(source_path: str, temporary_dir: Path) -> Path:
    """复制源文件并保留扩展名，让 Loader 读取与哈希完全相同的不可变字节。"""
    source = Path(source_path)
    snapshot = temporary_dir / f"source{source.suffix}"
    shutil.copy2(source, snapshot)
    return snapshot


def _restore_document_identity(
    document: Document,
    *,
    document_id: str,
    source_revision: str,
    source_path: str,
    collection: str,
) -> Document:
    """移除临时快照路径对领域身份的影响，并恢复用户文件名。"""
    metadata = dict(document.metadata)
    metadata.update(
        {
            "source_path": source_path,
            "collection": collection,
            "title": Path(source_path).stem,
            "source_revision": source_revision,
        }
    )
    return replace(document, id=document_id, metadata=metadata)


def _ingestion_revision(file_hash: str, loader_revision: object) -> str:
    revision = loader_revision.strip() if isinstance(loader_revision, str) else ""
    if not revision:
        return file_hash
    return hashlib.sha256(f"{file_hash}\0{revision}".encode()).hexdigest()


def _stamp_generation(
    chunks: list[Chunk],
    claim: ClaimHandle,
    source_path: str,
    collection: str,
) -> list[Chunk]:
    """在所有 Transform 之后写回控制面身份，防止适配器写入错误 generation。"""
    stamped: list[Chunk] = []
    for chunk in chunks:
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "source_path": source_path,
                "collection": collection,
                "doc_key": claim.doc_key,
                "generation": claim.generation,
                "source_revision": claim.source_revision,
            }
        )
        stamped.append(replace(chunk, metadata=metadata, source_ref=claim.source_revision))
    return stamped
