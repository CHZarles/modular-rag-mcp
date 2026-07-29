from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from libs.loader import FileIntegrityStore, SQLiteIntegrityStore


def test_compute_sha256_is_stable(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    content = b"stable document content"
    source.write_bytes(content)
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    first = store.compute_sha256(str(source))
    second = store.compute_sha256(str(source))

    assert first == second == hashlib.sha256(content).hexdigest()


def test_default_database_is_created_with_wal(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    store = SQLiteIntegrityStore()

    assert store.db_path == Path("data/db/ingestion_history.db")
    assert store.db_path.exists()
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_success_skip_is_scoped_by_collection_and_persists(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    db_path = tmp_path / "history.db"
    store = SQLiteIntegrityStore(db_path)
    file_hash = store.compute_sha256(str(source))

    store.mark_processing(file_hash, str(source), "docs")
    assert store.should_skip(file_hash, "docs") is False

    store.mark_success(file_hash, str(source), "docs", chunk_count=3)
    reopened = SQLiteIntegrityStore(db_path)

    assert reopened.should_skip(file_hash, "docs") is True
    assert reopened.should_skip(file_hash, "notes") is False
    assert reopened.list_processed("docs")[0]["chunk_count"] == 3

    reopened.remove_record(file_hash, "docs")
    assert reopened.should_skip(file_hash, "docs") is False


def test_failed_file_is_not_skipped(tmp_path: Path) -> None:
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    store.mark_failed("hash-1", "missing.pdf", "docs", "parse failed")

    assert store.should_skip("hash-1", "docs") is False
    assert store.list_processed()[0]["error_msg"] == "parse failed"


def test_concurrent_writes_keep_all_records(tmp_path: Path) -> None:
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    def write(index: int) -> None:
        store.mark_success(
            f"hash-{index}",
            f"document-{index}.pdf",
            "docs",
            chunk_count=index,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))

    assert len(store.list_processed("docs")) == 12
    assert isinstance(store, FileIntegrityStore)
