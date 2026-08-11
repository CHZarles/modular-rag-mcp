"""Rebuild the independent literal index from active BM25 Chunk records."""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.services import active_generation_counts  # noqa: E402
from src.core.settings import Settings, load_settings  # noqa: E402
from src.core.types import ChunkRecord, JsonDict  # noqa: E402
from src.ingestion.storage import BM25Indexer, SQLiteGrepIndex  # noqa: E402
from src.ingestion.storage.sqlite_grep_index import normalize_search_text  # noqa: E402
from src.libs.loader import SQLiteIntegrityStore  # noqa: E402


def rebuild_grep_index(settings: Settings) -> JsonDict:
    storage = settings.ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")
    integrity_path = _required_text(storage, "integrity_db_path", "ingestion.storage")
    bm25_path = _required_text(storage, "bm25_path", "ingestion.storage")
    db_path = Path(_required_text(settings.grep, "db_path", "grep")).expanduser()
    timeout_ms = settings.grep.get("timeout_ms", 1000)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        raise ValueError("Setting grep.timeout_ms must be a positive integer")

    integrity = SQLiteIntegrityStore(integrity_path)
    active, counts = active_generation_counts(integrity)
    collections = _active_collections(integrity.list_processed(), active)
    bm25 = BM25Indexer(bm25_path, generation_store=integrity)
    records = [
        record
        for collection in sorted(collections)
        for record in bm25.list_active_chunk_records(collection)
    ]

    building = db_path.with_name(f"{db_path.name}.building")
    for path in (building, Path(f"{building}-wal"), Path(f"{building}-shm")):
        path.unlink(missing_ok=True)
    index = SQLiteGrepIndex(building, timeout_ms=timeout_ms)
    index.initialize(wal=False)
    grouped: dict[tuple[str, str, int, str], list[ChunkRecord]] = defaultdict(list)
    for record in records:
        metadata = record.metadata
        grouped[
            (
                str(metadata["collection"]),
                str(metadata["doc_key"]),
                int(metadata["generation"]),
                str(metadata["source_path"]),
            )
        ].append(record)
    for batch in grouped.values():
        index.upsert(batch)
    index.check_ready(active, counts)
    _differential_check(index, records, active)
    _fsync_file(building)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(building, db_path)
    Path(f"{db_path}-wal").unlink(missing_ok=True)
    Path(f"{db_path}-shm").unlink(missing_ok=True)
    _fsync_directory(db_path.parent)
    return {
        "collections": len(collections),
        "documents": len(active),
        "chunks": len(records),
        "database": db_path.name,
    }


def _active_collections(rows: list[JsonDict], active: Mapping[str, int]) -> set[str]:
    return {
        str(row["collection"])
        for row in rows
        if row.get("attempt_status") == "published"
        and isinstance(row.get("doc_key"), str)
        and active.get(str(row["doc_key"])) == row.get("generation")
        and isinstance(row.get("collection"), str)
    }


def _differential_check(
    index: SQLiteGrepIndex,
    records: list[ChunkRecord],
    active: Mapping[str, int],
) -> None:
    eligible = [record for record in records if len(normalize_search_text(record.text)) >= 3]
    if not eligible:
        return
    rng = random.Random(20260810)
    sample = rng.sample(eligible, k=min(20, len(eligible)))
    for record in sample:
        normalized = normalize_search_text(record.text)
        start = rng.randrange(0, len(normalized) - 2)
        pattern = normalized[start : start + 3]
        collection = str(record.metadata["collection"])
        expected = [
            item.id
            for item in sorted(records, key=_stable_record_key)
            if item.metadata.get("collection") == collection
            and active.get(str(item.metadata.get("doc_key", "")))
            == item.metadata.get("generation")
            and pattern in normalize_search_text(item.text)
        ]
        actual = [
            row.chunk_id
            for row in index.iter_candidates(
                collection=collection,
                pattern=pattern,
                column="search_text",
                active_generations=active,
                deadline=time.monotonic() + 10,
            )
            if pattern in normalize_search_text(row.text)
        ]
        if actual != expected:
            raise RuntimeError("grep rebuild differential check failed")


def _stable_record_key(record: ChunkRecord) -> tuple[str, int, int, str]:
    metadata = record.metadata
    return (
        str(metadata.get("source_path", "")),
        _integer_or(metadata.get("page"), -1),
        _integer_or(metadata.get("chunk_index"), -1),
        record.id,
    )


def _integer_or(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _required_text(config: Mapping[str, object], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the independent literal grep index")
    parser.add_argument("--settings", default="config/settings.yaml")
    args = parser.parse_args(argv)
    result = rebuild_grep_index(load_settings(args.settings))
    print(
        "grep index rebuilt: "
        f"{result['collections']} collections, {result['documents']} documents, "
        f"{result['chunks']} chunks, {result['database']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
