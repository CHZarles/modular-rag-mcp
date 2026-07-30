from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from core.types import ClaimHandle
from libs.loader import FileIntegrityStore, SQLiteIntegrityStore


def _claim(
    store: SQLiteIntegrityStore,
    revision: str,
    source: Path,
    owner: str,
    *,
    collection: str = "docs",
    force: bool = False,
) -> ClaimHandle:
    result = store.try_claim(
        revision,
        str(source),
        collection,
        owner,
        60,
        force=force,
    )
    assert result.status == "acquired"
    assert result.handle is not None
    return result.handle


def test_content_revision_and_document_key_have_separate_identities(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    content = b"stable document content"
    source.write_bytes(content)
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    revision = store.compute_sha256(str(source))
    docs_key = store.compute_doc_key(str(source), "docs")
    notes_key = store.compute_doc_key(str(source), "notes")

    assert revision == hashlib.sha256(content).hexdigest()
    assert docs_key == store.compute_doc_key(str(source), "docs")
    assert docs_key != notes_key


def test_publish_makes_generation_active_and_same_revision_skips(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    db_path = tmp_path / "history.db"
    store = SQLiteIntegrityStore(db_path)

    first = _claim(store, "revision-1", source, "worker-1")
    assert first.generation == 1
    store.mark_staged(first)
    store.publish(first, chunk_count=3)

    skipped = SQLiteIntegrityStore(db_path).try_claim(
        "revision-1", str(source), "docs", "worker-2", 60
    )
    assert skipped.status == "already_succeeded"
    assert skipped.handle is None
    assert store.get_active_generations("docs") == {first.doc_key: 1}
    assert store.list_processed("docs")[0]["status"] == "success"


def test_publish_requires_staged_state_and_rolls_back_pointer_change(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")
    claim = _claim(store, "revision-1", source, "worker-1")

    with pytest.raises(RuntimeError, match="must be staged"):
        store.publish(claim, chunk_count=1)

    assert store.get_active_generations("docs") == {}
    store.mark_staged(claim)
    store.publish(claim, chunk_count=1)
    assert store.get_active_generations("docs") == {claim.doc_key: claim.generation}


def test_same_path_different_revision_uses_one_live_claim(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("v1", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    first = _claim(store, "revision-v1", source, "worker-a")
    second = store.try_claim("revision-v2", str(source), "docs", "worker-b", 60)

    assert second.status == "in_progress"
    assert second.handle is None
    assert first.doc_key == store.compute_doc_key(str(source), "docs")


def test_same_content_at_different_paths_is_not_accidentally_deduplicated(tmp_path: Path) -> None:
    first_source = tmp_path / "a.pdf"
    second_source = tmp_path / "b.pdf"
    first_source.write_text("same", encoding="utf-8")
    second_source.write_text("same", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    first = _claim(store, "same-revision", first_source, "worker-a")
    second = _claim(store, "same-revision", second_source, "worker-b")

    assert first.doc_key != second.doc_key


def test_concurrent_claim_allows_only_one_generation(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")
    barrier = Barrier(2)

    def claim(owner: str) -> str:
        barrier.wait()
        return store.try_claim("revision-1", str(source), "docs", owner, 60).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(claim, ["worker-a", "worker-b"]))

    assert sorted(statuses) == ["acquired", "in_progress"]


def test_takeover_fences_old_generation_and_only_new_generation_can_publish(
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    db_path = tmp_path / "history.db"
    store = SQLiteIntegrityStore(db_path)
    old = _claim(store, "revision-1", source, "worker-a")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE document_state SET lease_expires_at = 0 WHERE doc_key = ?",
            (old.doc_key,),
        )

    new = _claim(store, "revision-2", source, "worker-b")
    assert new.generation == old.generation + 1
    assert new.claim_token != old.claim_token

    with pytest.raises(RuntimeError, match="claim is no longer current"):
        store.mark_staged(old)
    with pytest.raises(RuntimeError, match="claim is no longer current"):
        store.publish(old, chunk_count=1)
    with pytest.raises(RuntimeError, match="claim is no longer current"):
        store.mark_failed(old, "late failure")
    with pytest.raises(RuntimeError, match="claim is no longer current"):
        store.renew_lease(old, 60)

    store.mark_staged(new)
    store.publish(new, chunk_count=2)
    assert store.get_active_generations("docs") == {new.doc_key: new.generation}
    assert old.generation in store.list_garbage_generations(old.doc_key)


def test_failed_generation_can_retry_without_changing_active_version(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")

    first = _claim(store, "revision-1", source, "worker-a")
    store.mark_failed(first, "parse failed")
    retry = _claim(store, "revision-1", source, "worker-b")

    assert retry.generation == first.generation + 1
    assert store.get_active_generations("docs") == {}
    assert first.generation in store.list_garbage_generations(first.doc_key)


def test_force_creates_new_generation_but_does_not_steal_live_claim(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")
    first = _claim(store, "revision-1", source, "worker-a")

    assert (
        store.try_claim("revision-1", str(source), "docs", "worker-b", 60, force=True).status
        == "in_progress"
    )
    store.mark_staged(first)
    store.publish(first, 1)

    forced = store.try_claim(
        "revision-1", str(source), "docs", "worker-b", 60, force=True
    )
    assert forced.status == "acquired"
    assert forced.handle is not None
    assert forced.handle.generation == first.generation + 1


def test_garbage_list_never_includes_active_or_currently_claimed_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.pdf"
    source.write_text("content", encoding="utf-8")
    store = SQLiteIntegrityStore(tmp_path / "history.db")
    active = _claim(store, "revision-1", source, "worker-a")
    store.mark_staged(active)
    store.publish(active, 1)
    claimed = _claim(store, "revision-2", source, "worker-b")

    assert store.list_garbage_generations(active.doc_key) == []
    store.mark_failed(claimed, "test cleanup")
    assert store.list_garbage_generations(active.doc_key) == [claimed.generation]


def test_existing_legacy_database_gets_generation_tables_without_data_loss(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE ingestion_history (
                file_hash TEXT NOT NULL,
                collection TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (file_hash, collection)
            )
            """
        )
        connection.execute(
            "INSERT INTO ingestion_history VALUES ('legacy', 'docs', 'old.pdf', 'success')"
        )

    SQLiteIntegrityStore(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy = connection.execute("SELECT file_hash FROM ingestion_history").fetchone()
    assert {"document_state", "ingestion_attempt"} <= tables
    assert legacy == ("legacy",)


def test_default_database_is_created_with_wal_and_protocol_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    store = SQLiteIntegrityStore()

    assert store.db_path == Path("data/db/ingestion_history.db")
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(store, FileIntegrityStore)
