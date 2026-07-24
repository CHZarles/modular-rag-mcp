"""scripts/ingest.py — CLI entry for offline ingestion.

Usage::

    python scripts/ingest.py --path path/to/file.pdf --collection docs [--force]

This is the C15 entry point. It wires up the default factories and runs
the :class:`IngestionPipeline`. Provider-specific configuration lives in
``config/settings.yaml``; this script does not override it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project importable when running from a fresh checkout.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from src.core.settings import load_settings  # noqa: E402
from src.ingestion.pipeline import IngestionPipeline  # noqa: E402
from src.ingestion.chunking.document_chunker import DocumentChunker  # noqa: E402
from src.ingestion.embedding.dense_encoder import DenseEncoder  # noqa: E402
from src.ingestion.embedding.sparse_encoder import SparseEncoder  # noqa: E402
from src.ingestion.storage.bm25_indexer import BM25IndexStore  # noqa: E402
from src.ingestion.storage.vector_upserter import VectorUpserter  # noqa: E402
from src.ingestion.transform.chunk_refiner import ChunkRefiner  # noqa: E402
from src.libs.loader.file_integrity import compute_sha256  # noqa: E402
from src.libs.loader.pdf_loader import PDFLoader  # noqa: E402
from src.libs.vector_store.chroma_store import ChromaStore  # noqa: E402


class _LocalIntegrity:
    """Tiny in-memory file-integrity stub for the CLI default flow."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], str] = {}

    def compute_sha256(self, source_path: str) -> str:
        return compute_sha256(source_path)

    def should_skip(self, file_hash: str, collection: str) -> bool:
        return self._seen.get((file_hash, collection)) == file_hash

    def mark_processing(self, file_hash, source_path, collection):
        self._seen[(file_hash, collection)] = file_hash

    def mark_success(self, file_hash, source_path, collection, chunk_count):
        self._seen[(file_hash, collection)] = file_hash

    def mark_failed(self, file_hash, source_path, collection, error):
        self._seen.pop((file_hash, collection), None)

    def remove_record(self, file_hash, collection):
        self._seen.pop((file_hash, collection), None)


def _build_pipeline(settings):
    return IngestionPipeline(
        integrity=_LocalIntegrity(),
        loader=PDFLoader(),
        chunker=DocumentChunker(
            # Splitter backend name resolution lives at a higher level;
            # use Recursive splitter as the default.
            splitter=None,  # type: ignore[arg-type]
        ) if False else _build_default(settings),
        transforms=[ChunkRefiner()],
        embedding=None,  # type: ignore[arg-type]
        sparse_encoder=SparseEncoder(),
        vector_store=ChromaStore(settings.vector_store),
        bm25_store=BM25IndexStore(),
        image_store=_NullImageStore(),
    )


def _build_default(settings):
    """Wire up default LLM/embedding/splitter providers from settings."""
    from src.ingestion.chunking.document_chunker import DocumentChunker
    from src.libs.embedding.embedding_factory import EmbeddingFactory
    from src.libs.splitter.recursive_splitter import RecursiveCharacterSplitter
    from src.libs.splitter.splitter_factory import SplitterFactory

    SplitterFactory.register("recursive", lambda: RecursiveCharacterSplitter())
    splitter = SplitterFactory.create("recursive")
    chunker = DocumentChunker(splitter)

    embedding = EmbeddingFactory.create(settings.embedding)
    return chunker


class _NullImageStore:
    def save_refs(self, images, trace=None): pass
    def get(self, image_id): return None
    def list_by_document(self, source_path, collection): return []
    def delete_by_document(self, source_path, collection): return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a document into the knowledge hub.")
    parser.add_argument("--path", required=True, help="Path to the PDF or text file to ingest.")
    parser.add_argument("--collection", default="default", help="Target collection name.")
    parser.add_argument("--force", action="store_true", help="Force re-ingestion even if hash matches.")
    args = parser.parse_args()

    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    pipeline = _build_pipeline(settings)

    request_path = Path(args.path)
    request = type("R", (), {
        "source_path": str(request_path),
        "collection": args.collection,
        "force": args.force,
    })()
    result = pipeline.run(request)  # type: ignore[arg-type]

    print(
        f"[ingest] status={result.status} chunks={result.chunk_count} "
        f"images={result.image_count} file_hash={getattr(result, 'file_hash', 'n/a')}"
    )
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())