from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.rebuild_grep_index import rebuild_grep_index
from src.core.services.grep_service import GrepService
from src.core.settings import Settings
from src.core.types import Chunk
from src.ingestion.embedding import SparseEncoder
from src.ingestion.storage import BM25Indexer, SQLiteGrepIndex
from src.libs.loader import SQLiteIntegrityStore


def test_rebuild_migrates_active_bm25_text_into_atomic_independent_database(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_text("migration needle text", encoding="utf-8")
    integrity = SQLiteIntegrityStore(tmp_path / "integrity.db")
    claim_result = integrity.try_claim(
        "revision-1",
        str(source),
        "docs",
        "test",
        60,
    )
    assert claim_result.handle is not None
    claim = claim_result.handle
    text = "migration needle text"
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    suffix = hashlib.sha256(f"0\0{content_hash}".encode()).hexdigest()[:32]
    chunk = Chunk(
        id=f"{claim.doc_key}:{claim.generation}:{suffix}",
        text=text,
        metadata={
            "collection": "docs",
            "doc_key": claim.doc_key,
            "generation": claim.generation,
            "source_path": str(source.resolve()),
            "chunk_index": 0,
            "page": 1,
        },
        source_ref=claim.source_revision,
        chunk_index=0,
    )
    bm25_path = tmp_path / "bm25"
    bm25 = BM25Indexer(bm25_path, generation_store=integrity)
    bm25.build([chunk], SparseEncoder().encode([chunk]))
    integrity.mark_staged(claim)
    integrity.publish(claim, 1)
    grep_path = tmp_path / "chunk_text.db"
    settings = _settings(tmp_path, bm25_path, grep_path)

    result = rebuild_grep_index(settings)

    assert result == {
        "collections": 1,
        "documents": 1,
        "chunks": 1,
        "database": "chunk_text.db",
    }
    assert grep_path.is_file()
    assert not Path(f"{grep_path}.building").exists()
    with patch.object(BM25Indexer, "query", side_effect=RuntimeError("BM25 offline")):
        response = GrepService(SQLiteGrepIndex(grep_path), integrity).search(
            pattern="needle",
            collection="docs",
        )
    assert [match.text for match in response.matches] == [text]


def test_rebuild_failure_preserves_the_previous_database(tmp_path: Path) -> None:
    bm25_path = tmp_path / "bm25"
    grep_path = tmp_path / "chunk_text.db"
    grep_path.write_bytes(b"previous database")
    settings = _settings(tmp_path, bm25_path, grep_path)

    with (
        patch("scripts.rebuild_grep_index._differential_check", side_effect=RuntimeError("fail")),
        pytest.raises(RuntimeError, match="fail"),
    ):
        rebuild_grep_index(settings)

    assert grep_path.read_bytes() == b"previous database"


def _settings(tmp_path: Path, bm25_path: Path, grep_path: Path) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "hash"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={
            "storage": {
                "integrity_db_path": str(tmp_path / "integrity.db"),
                "bm25_path": str(bm25_path),
            }
        },
        grep={
            "enabled": False,
            "db_path": str(grep_path),
            "timeout_ms": 1000,
        },
    )
