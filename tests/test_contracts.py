from __future__ import annotations

import unittest

from src.core.query_engine import (
    DenseRetriever,
    ExactMetadataFilter,
    HybridQueryEngine,
    HybridSearchConfig,
    NoneReranker,
    QueryProcessor,
    RRFFusion,
    SparseRetriever,
)
from src.core.response import ResponseBuilder
from src.core.trace import TraceContext
from src.core.types import (
    Chunk,
    Document,
    ImagePayload,
    IngestionRequest,
    QueryRequest,
    QueryResponse,
    RetrievalCandidate,
    SearchHit,
)
from src.ingestion.chunking import DocumentChunker
from src.ingestion.pipeline import IngestionPipeline
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.loader.base_loader import BaseLoader
from src.libs.llm.base_llm import BaseLLM, ChatResponse, Message
from src.libs.llm.base_vision_llm import ImageInput
from src.libs.reranker.base_reranker import BaseReranker
from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.mcp_server.tools import ToolHandler


class TestCoreContracts(unittest.TestCase):
    def test_query_response_round_trips_nested_contracts(self) -> None:
        candidate = RetrievalCandidate(
            chunk_id="chunk-1",
            text="Azure OpenAI setup",
            metadata={"source_path": "guide.pdf", "collection": "docs", "page": 3},
            score=0.9,
            source="dense",
            rank=1,
        )
        response = QueryResponse(
            answer="answer",
            citations=[],
            items=[candidate],
            images=[ImagePayload(image_id="img-1", mime_type="image/png", uri="img.png")],
            request_id="req-1",
        )

        restored = QueryResponse.from_dict(response.to_dict())

        self.assertEqual(restored.items[0].chunk_id, "chunk-1")
        self.assertEqual(restored.images[0].image_id, "img-1")
        self.assertEqual(restored.request_id, "req-1")

    def test_main_import_surface_is_available_without_concrete_providers(self) -> None:
        self.assertIsNotNone(BaseEmbedding)
        self.assertIsNotNone(BaseLoader)
        self.assertIsNotNone(BaseLLM)
        self.assertIsNotNone(BaseReranker)
        self.assertIsNotNone(BaseVectorStore)
        self.assertIsNotNone(ToolHandler)
        self.assertEqual(Message(role="user", content="hi").role, "user")
        self.assertEqual(ChatResponse(content="ok", model="fake").content, "ok")
        self.assertEqual(ImageInput(base64="abc").mime_type, "image/png")


class FakeSplitter:
    def split_text(self, text: str, trace: object | None = None) -> list[str]:
        return ["# Title", "Body [IMAGE: img-1]"]


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

    def mark_success(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        chunk_count: int,
    ) -> None:
        self.events.append(f"success:{chunk_count}")

    def mark_failed(self, file_hash: str, source_path: str, collection: str, error: str) -> None:
        self.events.append(f"failed:{error}")

    def remove_record(self, file_hash: str, collection: str) -> None:
        self.events.append("remove")

    def list_processed(self, collection: str | None = None) -> list[dict]:
        return []


class FakeLoader:
    supported_extensions = (".pdf",)

    def load(self, source_path: str, collection: str, trace: object | None = None) -> Document:
        return Document(
            id="doc-1",
            text="# Title\n\nBody [IMAGE: img-1]",
            metadata={
                "source_path": source_path,
                "collection": collection,
                "images": [
                    {
                        "id": "img-1",
                        "path": "images/img-1.png",
                        "text_offset": 14,
                        "text_length": 14,
                    }
                ],
            },
        )


class NoopTransform:
    name = "noop"

    def transform(self, chunks: list[Chunk], trace: object | None = None) -> list[Chunk]:
        return chunks


class FakeEmbedding:
    def embed(self, texts: list[str], trace: object | None = None) -> list[list[float]]:
        return [[float(index), 1.0] for index, _ in enumerate(texts)]


class FakeSparseEncoder:
    def encode(self, chunks: list[Chunk], trace: object | None = None) -> list[dict]:
        return [{"terms": chunk.text.split()} for chunk in chunks]


class FakeVectorStore:
    def __init__(self) -> None:
        self.records = []

    def upsert(self, records: list, trace: object | None = None) -> None:
        self.records = list(records)

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
        trace: object | None = None,
    ) -> list[SearchHit]:
        return [
            SearchHit(
                id="a",
                text="Dense hit",
                metadata={"collection": "docs", "source_path": "dense.pdf"},
                score=0.9,
                score_kind="similarity",
            )
        ][:top_k]

    def get_by_ids(self, ids: list[str]) -> list:
        return []

    def delete_by_metadata(self, filters: dict) -> int:
        return 0


class FakeBM25Store:
    def __init__(self) -> None:
        self.chunks = []

    def upsert(self, chunks: list[Chunk], sparse_vectors: list[dict], trace: object | None = None) -> None:
        self.chunks = list(chunks)

    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: dict | None = None,
        trace: object | None = None,
    ) -> list[SearchHit]:
        return [
            SearchHit(
                id="a",
                text="Sparse agrees",
                metadata={"collection": "docs", "source_path": "sparse.pdf"},
                score=8.0,
                score_kind="bm25",
            ),
            SearchHit(
                id="b",
                text="Sparse only",
                metadata={"collection": "docs", "source_path": "other.pdf"},
                score=4.0,
                score_kind="bm25",
            ),
        ][:top_k]

    def remove_document(self, source_path: str, collection: str) -> None:
        return None


class FakeImageStore:
    def __init__(self) -> None:
        self.images = []

    def save_refs(self, images: list, trace: object | None = None) -> None:
        self.images = list(images)

    def get(self, image_id: str):
        return None

    def list_by_document(self, source_path: str, collection: str) -> list:
        return []

    def delete_by_document(self, source_path: str, collection: str) -> int:
        return 0


class TestIngestionPipeline(unittest.TestCase):
    def test_pipeline_orchestrates_interfaces_and_stable_records(self) -> None:
        integrity = FakeIntegrity()
        vector_store = FakeVectorStore()
        bm25_store = FakeBM25Store()
        image_store = FakeImageStore()
        progress = []
        trace = TraceContext(trace_type="ingestion")

        pipeline = IngestionPipeline(
            integrity=integrity,
            loader=FakeLoader(),
            chunker=DocumentChunker(FakeSplitter()),
            transforms=[NoopTransform()],
            embedding=FakeEmbedding(),
            sparse_encoder=FakeSparseEncoder(),
            vector_store=vector_store,
            bm25_store=bm25_store,
            image_store=image_store,
        )

        result = pipeline.run(
            IngestionRequest(source_path="doc.pdf", collection="docs"),
            on_progress=lambda stage, step, total: progress.append((stage, step, total)),
            trace=trace,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.chunk_count, 2)
        self.assertEqual(result.image_count, 1)
        self.assertEqual(progress[0], ("integrity", 1, 7))
        self.assertEqual(progress[-1], ("complete", 7, 7))
        self.assertTrue(vector_store.records[0].content_hash)
        self.assertEqual(len(bm25_store.chunks), 2)
        self.assertEqual(image_store.images[0].image_id, "img-1")
        self.assertIn("success:2", integrity.events)

    def test_pipeline_skip_is_successful_control_flow(self) -> None:
        integrity = FakeIntegrity(skip=True)
        pipeline = IngestionPipeline(
            integrity=integrity,
            loader=FakeLoader(),
            chunker=DocumentChunker(FakeSplitter()),
            transforms=[],
            embedding=FakeEmbedding(),
            sparse_encoder=FakeSparseEncoder(),
            vector_store=FakeVectorStore(),
            bm25_store=FakeBM25Store(),
            image_store=FakeImageStore(),
        )

        result = pipeline.run(IngestionRequest(source_path="doc.pdf", collection="docs"))

        self.assertEqual(result.status, "skipped")
        self.assertEqual(integrity.events, ["compute", "skip"])


class TestQueryAndResponse(unittest.TestCase):
    def test_hybrid_query_engine_uses_dense_sparse_fusion_and_response_contract(self) -> None:
        engine = HybridQueryEngine(
            query_processor=QueryProcessor(),
            dense_retriever=DenseRetriever(FakeEmbedding(), FakeVectorStore()),
            sparse_retriever=SparseRetriever(FakeBM25Store()),
            fusion=RRFFusion(k=60),
            metadata_filter=ExactMetadataFilter(),
            reranker=NoneReranker(),
            config=HybridSearchConfig(dense_top_k=2, sparse_top_k=2, fusion_top_k=3),
        )

        request = QueryRequest(query="Azure OpenAI setup", collection="docs", top_k=2)
        candidates = engine.search(request)
        response = ResponseBuilder().build(request, candidates)

        self.assertEqual([candidate.chunk_id for candidate in candidates], ["a", "b"])
        self.assertEqual(response.items[0].source, "fusion")
        self.assertEqual(response.citations[0].chunk_id, "a")
        self.assertEqual(response.metadata["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
