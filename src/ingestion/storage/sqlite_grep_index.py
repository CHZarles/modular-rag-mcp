"""Independent SQLite FTS5 index for final ChunkRecord text."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.core.types import ChunkRecord, JsonDict

SCHEMA_VERSION = 1


class GrepIndexUnavailableError(RuntimeError):
    """Raised when the configured index cannot safely serve requests."""


class GrepQueryTimeoutError(TimeoutError):
    """Raised after SQLite interrupts a query at its monotonic deadline."""


@dataclass(frozen=True)
class GrepIndexRow:
    chunk_id: str
    text: str
    source_path: str
    page: int | None
    metadata: JsonDict


@dataclass(frozen=True)
class _PreparedRecord:
    chunk_id: str
    collection: str
    doc_key: str
    generation: int
    source_path: str
    page_order: int
    chunk_index: int
    text: str
    search_text: str
    metadata_json: str
    content_hash: str


class SQLiteGrepIndex:
    """Store and stream literal-search candidates without loading the corpus."""

    def __init__(self, db_path: str | Path, *, timeout_ms: int = 1000) -> None:
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError("grep configuration error: timeout_ms must be a positive integer")
        self.db_path = Path(db_path).expanduser()
        self.timeout_ms = timeout_ms

    def initialize(self, *, wal: bool = True) -> None:
        """Create a new version-1 database; callers choose when files may be created."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect(require_existing=False)) as connection, connection:
            connection.execute(f"PRAGMA journal_mode = {'WAL' if wal else 'DELETE'}")
            connection.executescript(_SCHEMA_SQL)

    def upsert(self, records: list[ChunkRecord]) -> None:
        if not records:
            return
        prepared = [_prepare_record(record) for record in records]
        identity = {
            (row.collection, row.doc_key, row.generation, row.source_path)
            for row in prepared
        }
        if len(identity) != 1:
            raise ValueError("grep index error: one batch must contain exactly one generation")
        if len({row.chunk_id for row in prepared}) != len(prepared):
            raise ValueError("grep index error: duplicate chunk_id in batch")
        if len({row.chunk_index for row in prepared}) != len(prepared):
            raise ValueError("grep index error: duplicate chunk_index in batch")

        _, doc_key, generation, _ = next(iter(identity))
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            connection.execute(
                "DELETE FROM chunks WHERE doc_key = ? AND generation = ?",
                (doc_key, generation),
            )
            connection.executemany(
                """
                INSERT INTO chunks (
                    chunk_id, collection, doc_key, generation, source_path,
                    page_order, chunk_index, text, search_text, metadata_json,
                    content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.chunk_id,
                        row.collection,
                        row.doc_key,
                        row.generation,
                        row.source_path,
                        row.page_order,
                        row.chunk_index,
                        row.text,
                        row.search_text,
                        row.metadata_json,
                        row.content_hash,
                    )
                    for row in prepared
                ],
            )

    def iter_candidates(
        self,
        *,
        collection: str,
        pattern: str,
        column: Literal["text", "search_text"],
        active_generations: Mapping[str, int],
        deadline: float,
    ) -> Iterator[GrepIndexRow]:
        if not active_generations:
            return
        if column not in {"text", "search_text"}:
            raise ValueError("grep query error: unsupported FTS column")
        now = time.monotonic()
        if now >= deadline:
            raise GrepQueryTimeoutError("grep query timed out")
        escaped = pattern.replace('"', '""')
        match_query = f'{column} : "{escaped}"'
        remaining_ms = max(1, min(self.timeout_ms, int((deadline - now) * 1000)))

        with closing(self._connect(timeout_ms=remaining_ms)) as connection:
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() >= deadline else 0,
                1000,
            )
            try:
                self._require_schema(connection)
                connection.execute(
                    "CREATE TEMP TABLE active_grep_generations "
                    "(doc_key TEXT PRIMARY KEY, generation INTEGER NOT NULL) WITHOUT ROWID"
                )
                connection.executemany(
                    "INSERT INTO active_grep_generations(doc_key, generation) VALUES (?, ?)",
                    sorted(active_generations.items()),
                )
                cursor = connection.execute(
                    """
                    SELECT c.chunk_id, c.text, c.source_path, c.page_order, c.metadata_json
                    FROM chunks_fts
                    JOIN chunks AS c ON c.rowid = chunks_fts.rowid
                    JOIN active_grep_generations AS active
                      ON active.doc_key = c.doc_key
                     AND active.generation = c.generation
                    WHERE c.collection = ? AND chunks_fts MATCH ?
                    ORDER BY c.source_path ASC, c.page_order ASC,
                             c.chunk_index ASC, c.chunk_id ASC
                    """,
                    (collection, match_query),
                )
                for row in cursor:
                    if time.monotonic() >= deadline:
                        raise GrepQueryTimeoutError("grep query timed out")
                    metadata = json.loads(row["metadata_json"])
                    if not isinstance(metadata, dict):
                        raise GrepIndexUnavailableError("grep index metadata is invalid")
                    page_order = int(row["page_order"])
                    yield GrepIndexRow(
                        chunk_id=str(row["chunk_id"]),
                        text=str(row["text"]),
                        source_path=str(row["source_path"]),
                        page=page_order if page_order > 0 else None,
                        metadata=metadata,
                    )
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc).lower() and time.monotonic() >= deadline:
                    raise GrepQueryTimeoutError("grep query timed out") from None
                raise GrepIndexUnavailableError("grep index query failed") from exc
            finally:
                connection.set_progress_handler(None, 0)

    def remove_generation(self, doc_key: str, generation: int) -> int:
        with closing(self._connect()) as connection, connection:
            self._require_schema(connection)
            cursor = connection.execute(
                "DELETE FROM chunks WHERE doc_key = ? AND generation = ?",
                (doc_key, generation),
            )
            return cursor.rowcount

    def remove_document(self, source_path: str, collection: str) -> int:
        with closing(self._connect()) as connection, connection:
            self._require_schema(connection)
            cursor = connection.execute(
                "DELETE FROM chunks WHERE source_path = ? AND collection = ?",
                (source_path, collection),
            )
            return cursor.rowcount

    def check_ready(
        self,
        active_generations: Mapping[str, int],
        expected_chunk_counts: Mapping[str, int],
    ) -> None:
        if set(active_generations) != set(expected_chunk_counts):
            raise GrepIndexUnavailableError("grep index active count metadata is incomplete")
        try:
            with closing(self._connect()) as connection:
                self._require_schema(connection)
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise GrepIndexUnavailableError("grep index integrity check failed")
                connection.execute(
                    "INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)"
                )
                actual = _active_counts(connection, active_generations)
        except GrepIndexUnavailableError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise GrepIndexUnavailableError("grep index is unavailable") from exc
        expected = {doc_key: int(count) for doc_key, count in expected_chunk_counts.items()}
        if actual != expected:
            raise GrepIndexUnavailableError("grep index active chunk count mismatch")

    def _connect(
        self,
        *,
        require_existing: bool = True,
        timeout_ms: int | None = None,
    ) -> sqlite3.Connection:
        if require_existing and not self.db_path.is_file():
            raise GrepIndexUnavailableError("grep index database does not exist")
        effective_timeout_ms = self.timeout_ms if timeout_ms is None else timeout_ms
        connection = sqlite3.connect(
            self.db_path,
            timeout=effective_timeout_ms / 1000.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {effective_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _require_schema(connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            raise GrepIndexUnavailableError("grep index rebuild required")


def normalize_search_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _prepare_record(record: ChunkRecord) -> _PreparedRecord:
    if not isinstance(record.text, str):
        raise ValueError("grep index error: text must be a string")
    metadata = record.metadata
    if not isinstance(metadata, dict):
        raise ValueError("grep index error: metadata must be a mapping")
    collection = _required_text(metadata, "collection")
    if Path(collection).name != collection or collection in {".", ".."}:
        raise ValueError("grep index error: collection must be a simple name")
    doc_key = _required_text(metadata, "doc_key")
    source_path = _required_text(metadata, "source_path")
    generation = _integer(metadata.get("generation"), "generation", minimum=1)
    chunk_index = _integer(metadata.get("chunk_index"), "chunk_index", minimum=0)
    page = metadata.get("page")
    page_order = page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else -1
    content_hash = hashlib.sha256(record.text.encode("utf-8")).hexdigest()
    if record.content_hash != content_hash:
        raise ValueError("grep index error: content_hash does not match text")
    suffix = hashlib.sha256(f"{chunk_index}\0{content_hash}".encode()).hexdigest()[:32]
    expected_id = f"{doc_key}:{generation}:{suffix}"
    if record.id != expected_id:
        raise ValueError("grep index error: chunk id does not match final storage identity")
    try:
        metadata_json = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("grep index error: metadata must be JSON serializable") from exc
    return _PreparedRecord(
        chunk_id=record.id,
        collection=collection,
        doc_key=doc_key,
        generation=generation,
        source_path=source_path,
        page_order=page_order,
        chunk_index=chunk_index,
        text=record.text,
        search_text=normalize_search_text(record.text),
        metadata_json=metadata_json,
        content_hash=content_hash,
    )


def _active_counts(
    connection: sqlite3.Connection,
    active_generations: Mapping[str, int],
) -> dict[str, int]:
    if not active_generations:
        return {}
    connection.execute(
        "CREATE TEMP TABLE ready_grep_generations "
        "(doc_key TEXT PRIMARY KEY, generation INTEGER NOT NULL) WITHOUT ROWID"
    )
    connection.executemany(
        "INSERT INTO ready_grep_generations(doc_key, generation) VALUES (?, ?)",
        sorted(active_generations.items()),
    )
    rows = connection.execute(
        """
        SELECT active.doc_key, COUNT(chunks.rowid) AS chunk_count
        FROM ready_grep_generations AS active
        LEFT JOIN chunks
          ON chunks.doc_key = active.doc_key
         AND chunks.generation = active.generation
        GROUP BY active.doc_key
        """
    ).fetchall()
    return {str(row["doc_key"]): int(row["chunk_count"]) for row in rows}


def _required_text(metadata: JsonDict, key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"grep index error: {key} must be non-empty text")
    return value


def _integer(value: object, field: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"grep index error: {field} must be an integer >= {minimum}")
    return value


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY,
    chunk_id TEXT UNIQUE NOT NULL,
    collection TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    generation INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    page_order INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    search_text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_generation
    ON chunks(doc_key, generation);
CREATE INDEX IF NOT EXISTS idx_chunks_stable_order
    ON chunks(collection, source_path, page_order, chunk_index, chunk_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    search_text,
    content='chunks',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, search_text)
    VALUES (new.rowid, new.text, new.search_text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, search_text)
    VALUES ('delete', old.rowid, old.text, old.search_text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, search_text)
    VALUES ('delete', old.rowid, old.text, old.search_text);
    INSERT INTO chunks_fts(rowid, text, search_text)
    VALUES (new.rowid, new.text, new.search_text);
END;

PRAGMA user_version = 1;
"""


__all__ = [
    "GrepIndexRow",
    "GrepIndexUnavailableError",
    "GrepQueryTimeoutError",
    "SQLiteGrepIndex",
    "normalize_search_text",
]
