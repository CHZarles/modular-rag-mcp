from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.types import ChunkRecord
from src.ingestion.storage.sqlite_grep_index import (
    GrepIndexUnavailableError,
    GrepQueryTimeoutError,
    SQLiteGrepIndex,
    normalize_search_text,
)


def _record(
    text: str,
    *,
    doc_key: str = "d" * 64,
    generation: int = 1,
    chunk_index: int = 0,
    source_path: str = "/docs/manual.pdf",
) -> ChunkRecord:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    suffix = hashlib.sha256(f"{chunk_index}\0{content_hash}".encode()).hexdigest()[:32]
    return ChunkRecord(
        id=f"{doc_key}:{generation}:{suffix}",
        text=text,
        metadata={
            "collection": "docs",
            "doc_key": doc_key,
            "generation": generation,
            "source_path": source_path,
            "chunk_index": chunk_index,
            "page": chunk_index + 1,
        },
        content_hash=content_hash,
    )


def test_index_is_explicitly_created_and_streams_only_active_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "grep.db"
    index = SQLiteGrepIndex(db_path)
    assert not db_path.exists()

    index.initialize()
    old = _record("Old needle", generation=1)
    current = _record("Current needle", generation=2)
    index.upsert([old])
    index.upsert([current])

    rows = list(
        index.iter_candidates(
            collection="docs",
            pattern="needle",
            column="text",
            active_generations={"d" * 64: 2},
            deadline=time.monotonic() + 1,
        )
    )

    assert [row.chunk_id for row in rows] == [current.id]
    assert rows[0].text == "Current needle"


def test_upsert_validates_the_whole_generation_before_replacing_rows(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    original = _record("stable text")
    index.upsert([original])

    invalid = replace(_record("replacement text"), content_hash="wrong")
    with pytest.raises(ValueError, match="content_hash"):
        index.upsert([invalid])

    rows = list(
        index.iter_candidates(
            collection="docs",
            pattern="stable",
            column="text",
            active_generations={"d" * 64: 1},
            deadline=time.monotonic() + 1,
        )
    )
    assert [row.chunk_id for row in rows] == [original.id]


def test_upsert_rolls_back_the_generation_when_an_sql_trigger_fails(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    original = [_record("stable first", chunk_index=0), _record("stable second", chunk_index=1)]
    index.upsert(original)
    with sqlite3.connect(index.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_second_insert BEFORE INSERT ON chunks "
            "WHEN new.chunk_index = 1 BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
        )

    replacement = [
        _record("replacement first", chunk_index=0),
        _record("replacement second", chunk_index=1),
    ]
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        index.upsert(replacement)

    rows = list(
        index.iter_candidates(
            collection="docs",
            pattern="stable",
            column="text",
            active_generations={"d" * 64: 1},
            deadline=time.monotonic() + 1,
        )
    )
    assert [row.chunk_id for row in rows] == [record.id for record in original]


def test_fts_candidates_match_python_literal_scan_for_multilingual_text(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    records = [
        _record('错误代码 "x"   .*[ ] AND OR', doc_key="a" * 64, source_path="/a.txt"),
        _record("Straße ＡＢＣ", doc_key="b" * 64, source_path="/b.txt"),
        _record("Cafe\u0301 Ελληνικά العربية 한글 😀", doc_key="c" * 64, source_path="/c.txt"),
    ]
    for record in records:
        index.upsert([record])
    active = {key * 64: 1 for key in "abc"}

    for pattern, column in [
        ("错误代码", "text"),
        ('"x"', "text"),
        ("   ", "text"),
        (".*[", "text"),
        ("AND OR", "text"),
        ("strasse", "search_text"),
        ("abc", "search_text"),
        ("café", "search_text"),
        ("الع", "search_text"),
        ("한글 ", "search_text"),
    ]:
        transform = normalize_search_text if column == "search_text" else lambda value: value
        expected = [record.id for record in records if pattern in transform(record.text)]
        actual = [
            row.chunk_id
            for row in index.iter_candidates(
                collection="docs",
                pattern=pattern,
                column=column,  # type: ignore[arg-type]
                active_generations=active,
                deadline=time.monotonic() + 1,
            )
            if pattern in transform(row.text)
        ]
        assert actual == expected, pattern


def test_ready_counts_and_removal_are_generation_aware(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    first = _record("first needle", generation=1)
    second = _record("second needle", generation=2)
    index.upsert([first])
    index.upsert([second])

    index.check_ready({"d" * 64: 2}, {"d" * 64: 1})
    with pytest.raises(GrepIndexUnavailableError, match="count mismatch"):
        index.check_ready({"d" * 64: 2}, {"d" * 64: 2})

    assert index.remove_generation("d" * 64, 1) == 1
    assert index.remove_document("/docs/manual.pdf", "docs") == 1
    index.check_ready({}, {})


def test_ready_rejects_missing_wrong_version_and_corrupt_databases(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.db"
    with pytest.raises(GrepIndexUnavailableError, match="does not exist"):
        SQLiteGrepIndex(missing_path).check_ready({}, {})
    assert not missing_path.exists()

    wrong_version = SQLiteGrepIndex(tmp_path / "wrong-version.db")
    wrong_version.initialize()
    with sqlite3.connect(wrong_version.db_path) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(GrepIndexUnavailableError, match="rebuild required"):
        wrong_version.check_ready({}, {})

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_bytes(b"not a sqlite database")
    with pytest.raises(GrepIndexUnavailableError, match="unavailable"):
        SQLiteGrepIndex(corrupt_path).check_ready({}, {})

    inconsistent_fts = SQLiteGrepIndex(tmp_path / "inconsistent-fts.db")
    inconsistent_fts.initialize()
    record = _record("indexed needle")
    inconsistent_fts.upsert([record])
    with sqlite3.connect(inconsistent_fts.db_path) as connection:
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")
    with pytest.raises(GrepIndexUnavailableError, match="unavailable"):
        inconsistent_fts.check_ready({"d" * 64: 1}, {"d" * 64: 1})


def test_expired_deadline_stops_before_scanning(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    index.upsert([_record("needle text")])

    started = time.monotonic()
    with pytest.raises(GrepQueryTimeoutError):
        list(
            index.iter_candidates(
                collection="docs",
                pattern="needle",
                column="text",
                active_generations={"d" * 64: 1},
                deadline=started - 1,
            )
        )
    assert time.monotonic() - started < 0.25


def test_progress_handler_interrupts_a_running_sql_query(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    records = [_record(f"needle {number}", chunk_index=number) for number in range(500)]
    index.upsert(records)
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls == 1 else 2.0

    with (
        patch("src.ingestion.storage.sqlite_grep_index.time.monotonic", side_effect=clock),
        pytest.raises(GrepQueryTimeoutError),
    ):
        list(
            index.iter_candidates(
                collection="docs",
                pattern="needle",
                column="text",
                active_generations={"d" * 64: 1},
                deadline=1.0,
            )
        )

    assert clock_calls >= 3
