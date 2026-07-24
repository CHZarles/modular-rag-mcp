"""C14 tests: IngestionPipeline integration with fakes."""

from __future__ import annotations

from src.core.types import (
    Chunk,
    ChunkRecord,
    ImageRef,
    ImagePayload,
    IngestionRequest,
    SearchHit,
)
from src.ingestion.pipeline import IngestionPipeline
from src.ports.ingestion import (
    BaseEmbedding,
    BaseLoader,
    BaseSplitter,
    BaseTransform,
    BaseVectorStore,
    SparseEncoder,
)


class FakeIntegrity:
    def __init__(self, skip: bool = False) -> None:
        self.skip = skip
        self.events: list[str] = []

    def compute_sha256(self, source_path: str) -> str:
        self.events.append("compute")
        return "hash-1"

    def should_skip(self, file_hash: str, collection: str) -> bool:
        self.events.append("skip")
        return self.skip

    def mark_processing(self, file_hash: str, source_path: str, collection: str) -> None:
        self.events.append("processing")

    def mark_success(self, file_hash, source_path, collection, chunk_count) -> None:
        self.events.append(f"success:{chunk_count}")

    def mark_failed(self, file_hash, source_path, collection, error) -> None:
        self.events.append(f"failed:{error}")

    def remove_record(self, file_hash, collection) -> None:
        self.events.append("remove")


class FakeLoader(BaseLoader):
    supported_extensions = (".pdf", ".txt")

    def load(self, source_path, collection, trace=None):
        return _FakeDocument(
            id="doc1",
            text="# Title\n\nBody [IMAGE: img-1] text.",
            metadata={
                "source_path": source_path,
                "collection": collection,
                "images": [{"id": "img-1", "path": "/tmp/i.png", "page": 1}],
            },
        )


class _FakeDocument:
    """Duck-typed Document — IngestionPipeline imports the class via type annotation."""

    def __init__(self, id: str, text: str, metadata: dict) -> None:
        self.id = id
        self.text = text
        self.metadata = metadata


class FakeSplitter(BaseSplitter):
    def split_text(self, text, trace=None):
        return ["# Title", "Body [IMAGE: img-1] text."]


class NoopTransform(BaseTransform):
    name = "noop"

    def transform(self, chunks, trace=None):
        return chunks


class FakeEmbedding(BaseEmbedding):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts, trace=None):
        self.calls.append(list(texts))
        return [[float(i), 1.0] for i in range(len(texts))]


class FakeSparseEncoder:
    def encode(self, chunks, trace=None):
        return [{"terms": {t: 1}} for t in [c.text for c in chunks]]


class FakeVectorStore(BaseVectorStore):
    def __init__(self) -> None:
        self.records: list[ChunkRecord] = []

    def upsert(self, records, trace=None):
        self.records = list(records)

    def query(self, vector, top_k, filters=None, trace=None):
        return []

    def get_by_ids(self, ids):
        return []

    def delete_by_metadata(self, filters):
        return 0


class FakeBM25Store:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.sparse_vectors: list[dict] = []

    def upsert(self, chunks, sparse_vectors, trace=None):
        self.chunks = list(chunks)
        self.sparse_vectors = list(sparse_vectors)

    def query(self, keywords, top_k, filters=None, trace=None):
        return []

    def remove_document(self, source_path, collection):
        pass


class FakeImageStore:
    def __init__(self) -> None:
        self.images: list[ImageRef] = []

    def save_refs(self, images, trace=None):
        self.images = list(images)

    def get(self, image_id):
        return None

    def list_by_document(self, source_path, collection):
        return []

    def delete_by_document(self, source_path, collection):
        return 0


def _build_pipeline(integrity_skip: bool = False) -> tuple[IngestionPipeline, dict]:
    integrity = FakeIntegrity(skip=integrity_skip)
    vector_store = FakeVectorStore()
    pipeline = IngestionPipeline(
        integrity=integrity,
        loader=FakeLoader(),
        chunker=_FakeChunker(FakeSplitter()),
        transforms=[NoopTransform()],
        embedding=FakeEmbedding(),
        sparse_encoder=FakeSparseEncoder(),
        vector_store=vector_store,
        bm25_store=FakeBM25Store(),
        image_store=FakeImageStore(),
    )
    return pipeline, {"integrity": integrity, "vs": vector_store}


class _FakeChunker:
    def __init__(self, splitter):
        from src.core.types import Chunk as _Chunk
        self.splitter = splitter
        self._Chunk = _Chunk

    def split_document(self, document, trace=None):
        texts = self.splitter.split_text(document.text, trace=trace)
        chunks = []
        for idx, t in enumerate(texts):
            chunks.append(
                Chunk(
                    id=f"doc1:chunk:{idx}",
                    text=t,
                    metadata=dict(document.metadata),
                    source_ref=document.id,
                    chunk_index=idx,
                )
            )
        return chunks


def test_pipeline_runs_full_chain() -> None:
    pipeline, ctx = _build_pipeline()
    progress: list[tuple[str, int, int]] = []
    request = IngestionRequest(source_path="a.pdf", collection="docs")
    result = pipeline.run(
        request,
        on_progress=lambda stage, step, total: progress.append((stage, step, total)),
    )
    assert result.status == "success"
    assert result.chunk_count == 2
    assert len(ctx["vs"].records) == 2
    assert "success:2" in ctx["integrity"].events


def test_pipeline_skips_when_integrity_says_so() -> None:
    pipeline, ctx = _build_pipeline(integrity_skip=True)
    result = pipeline.run(IngestionRequest(source_path="a.pdf", collection="docs"))
    assert result.status == "skipped"
    assert ctx["vs"].records == []


def test_pipeline_emits_progress_callbacks() -> None:
    pipeline, _ = _build_pipeline()
    seen = []
    pipeline.run(
        IngestionRequest(source_path="a.pdf", collection="docs"),
        on_progress=lambda stage, step, total: seen.append((stage, step, total)),
    )
    assert seen[0] == ("integrity", 1, 7)
    assert seen[-1] == ("complete", 7, 7)