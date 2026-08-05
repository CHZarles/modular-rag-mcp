"""SQLiteTraceStore contract tests (plan §C2.2 / §6.4–§6.7 / §12).

Covers:
* schema bootstrap (create-if-not-exists, PRAGMAs)
* payload validation (negative elapsed, missing required fields, bad JSON, dup trace_id)
* recent list ordering and limits
* retention purge boundary
* purge-by-actor / purge-by-before composition
* check_writable() probe
* separate-path guard against the ingestion integrity DB
* 100-thread concurrent insert without lost rows or lock errors
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.trace import (
    SQLiteTraceStore,
    TraceCollector,
    TraceContext,
    TraceStoreError,
)
from src.core.types import JsonDict

# --- Helpers --------------------------------------------------------------

@pytest.fixture
def traces_db(tmp_path: Path) -> Iterator[SQLiteTraceStore]:
    store = SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)
    yield store
    # Connection closes per call; nothing to clean up.


def _trace(
    *,
    trace_id: str | None = None,
    trace_type: str = "query",
    elapsed_ms: float = 12.5,
    metadata: JsonDict | None = None,
    started_at: datetime | None = None,
) -> TraceContext:
    trace = TraceContext(trace_type=trace_type)
    if trace_id is not None:
        trace.trace_id = trace_id
    if started_at is not None:
        trace.started_at = started_at.isoformat()
    trace.record_stage(
        "dense_retrieval",
        {"method": "dense", "provider": "Fake"},
        elapsed_ms=2.5,
    )
    trace.metadata.update(metadata or {})
    # Patch elapsed by finishing after a sleep or set manually.
    trace._finish_mono = trace._start_mono + (elapsed_ms / 1000.0)  # type: ignore[attr-defined]
    trace.finished_at = (
        datetime.fromisoformat(trace.started_at) + timedelta(milliseconds=elapsed_ms)
    ).isoformat()
    trace.finished_at = trace.finished_at
    return trace


def _insert(store: SQLiteTraceStore, **kwargs: object) -> None:
    store.collect(_trace(**kwargs))


# --- Schema bootstrap & instance contract ---------------------------------

def test_store_satisfies_trace_collector_protocol(traces_db: SQLiteTraceStore) -> None:
    assert isinstance(traces_db, TraceCollector)


def test_bootstrap_creates_table_indexes_and_version(traces_db: SQLiteTraceStore) -> None:
    with sqlite3.connect(traces_db.path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trace_event'"
        )}
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trace_event'"
        )}
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert "trace_event" in tables
    assert {"idx_trace_type_started", "idx_trace_started",
            "idx_trace_actor_started", "idx_trace_query_started"} <= indexes
    assert version == 1


def test_bootstrap_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    # Bootstrap with a fresh path.
    SQLiteTraceStore(tmp_path / "fresh.db", auto_purge=False)
    with sqlite3.connect(tmp_path / "fresh.db") as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_reopen_store_reads_previously_persisted_rows(tmp_path: Path) -> None:
    path = tmp_path / "reopen.db"
    writer = SQLiteTraceStore(path, auto_purge=False)
    _insert(writer, trace_id="abc", metadata={"query": "x", "actor_key": "a"})
    del writer

    reader = SQLiteTraceStore(path, auto_purge=False)
    rows = reader.list_recent("query", limit=10)

    assert [row["trace_id"] for row in rows] == ["abc"]


# --- Payload validation ---------------------------------------------------

def test_collect_persists_stable_contract_fields(traces_db: SQLiteTraceStore) -> None:
    _insert(traces_db, trace_id="trace-1", metadata={
        "query": "hello",
        "query_normalized": "hello",
        "collection": "docs",
        "top_k": 5,
        "actor_key": "actor-1",
        "session_id": "sess-1",
        "request_id": "req-1",
        "status": "success",
        "result_count": 0,
        "zero_result": True,
    })

    payload = traces_db.list_recent("query", limit=1)[0]

    assert payload["trace_id"] == "trace-1"
    assert payload["schema_version"] == 1
    assert payload["trace_type"] == "query"
    assert payload["metadata"]["actor_key"] == "actor-1"
    assert payload["metadata"]["status"] == "success"
    assert payload["metadata"]["result_count"] == 0


def test_duplicate_trace_id_is_rejected(traces_db: SQLiteTraceStore) -> None:
    _insert(traces_db, trace_id="dup")

    with pytest.raises(TraceStoreError, match="already persisted"):
        _insert(traces_db, trace_id="dup")


def test_negative_elapsed_is_rejected(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "negative.db", auto_purge=False)
    # Negative elapsed violates the schema CHECK, so the store cannot insert
    # it; we expect a TraceStoreError either at validation or sqlite layer.
    trace = TraceContext(trace_type="query")
    trace.metadata["dummy"] = "x"
    with pytest.raises(TraceStoreError):
        store.collect(trace)
        # Force negative elapsed via finish manipulation.
        # Use a manual negative elapsed payload.
        bad = TraceContext(trace_type="query")
        bad.metadata["x"] = "y"
        # Monkey-patch elapsed to be negative by reaching into internals.
        object.__setattr__(bad, "_finish_mono", bad._start_mono - 0.005)  # type: ignore[attr-defined]
        bad.finish()
        store.collect(bad)


def test_invalid_metadata_status_type_is_rejected(traces_db: SQLiteTraceStore) -> None:
    trace = TraceContext(trace_type="query")
    trace.metadata["status"] = 123
    with pytest.raises(TraceStoreError):
        traces_db.collect(trace)


def test_unserialisable_payload_is_rejected(traces_db: SQLiteTraceStore) -> None:
    # Any valid trace should serialise; we add a deliberate circular value.
    trace = TraceContext(trace_type="query")
    circular: dict[str, object] = {}
    circular["self"] = circular
    trace.metadata["circular"] = circular  # JSON cannot represent this
    with pytest.raises(TraceStoreError):
        traces_db.collect(trace)


# --- Retention / purge ----------------------------------------------------

def test_retention_days_outside_allowed_range_rejected(tmp_path: Path) -> None:
    with pytest.raises(TraceStoreError):
        SQLiteTraceStore(tmp_path / "bad.db", retention_days=0, auto_purge=False)
    with pytest.raises(TraceStoreError):
        SQLiteTraceStore(tmp_path / "bad.db", retention_days=-1, auto_purge=False)
    with pytest.raises(TraceStoreError):
        SQLiteTraceStore(tmp_path / "bad.db", retention_days=5000, auto_purge=False)


def test_purge_expired_removes_only_old_rows(traces_db: SQLiteTraceStore) -> None:
    now = datetime.now(timezone.utc)
    old_started = (now - timedelta(days=120)).isoformat()
    fresh_started = (now - timedelta(days=2)).isoformat()
    _insert(traces_db, trace_id="old", started_at=datetime.fromisoformat(old_started))
    _insert(traces_db, trace_id="fresh", started_at=datetime.fromisoformat(fresh_started))

    store = SQLiteTraceStore(
        traces_db.path, retention_days=90, auto_purge=False
    )

    deleted = store.purge_expired(now=now)

    assert deleted == 1
    remaining = {row["trace_id"] for row in store.list_recent("query", limit=10)}
    assert remaining == {"fresh"}


def test_purge_requires_at_least_one_filter(traces_db: SQLiteTraceStore) -> None:
    with pytest.raises(TraceStoreError, match="at least one"):
        traces_db.purge()


def test_purge_combines_before_and_actor_with_and(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "and.db", auto_purge=False)
    now = datetime.now(timezone.utc)
    store.collect(_trace(
        trace_id="t1",
        started_at=now - timedelta(days=10),
        metadata={"actor_key": "alice"},
    ))
    store.collect(_trace(
        trace_id="t2",
        started_at=now - timedelta(days=10),
        metadata={"actor_key": "bob"},
    ))
    store.collect(_trace(
        trace_id="t3",
        started_at=now - timedelta(days=2),
        metadata={"actor_key": "alice"},
    ))

    deleted = store.purge(before=now - timedelta(days=5), actor_key="alice")

    assert deleted == 1
    remaining = {row["trace_id"] for row in store.list_recent("query", limit=10)}
    assert remaining == {"t2", "t3"}


def test_auto_purge_runs_once_per_24h(tmp_path: Path) -> None:
    store = SQLiteTraceStore(
        tmp_path / "auto.db",
        retention_days=90,
        auto_purge=True,
    )
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    fresh = now - timedelta(days=2)
    store.collect(_trace(trace_id="old", started_at=old))
    store.collect(_trace(trace_id="fresh", started_at=fresh))

    deleted_first = store.maybe_auto_purge(now=now)
    deleted_second = store.maybe_auto_purge(now=now + timedelta(hours=2))
    deleted_third = store.maybe_auto_purge(now=now + timedelta(days=2))

    assert deleted_first == 1
    assert deleted_second == 0  # already ran today
    assert deleted_third == 0  # only one expired row

    remaining = {row["trace_id"] for row in store.list_recent("query", limit=10)}
    assert remaining == {"fresh"}


# --- list_recent ----------------------------------------------------------

def test_list_recent_orders_by_started_at_and_trace_id_desc(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "order.db", auto_purge=False)
    store.collect(_trace(trace_id="a", metadata={"started_marker": "earliest"}))
    time.sleep(0.01)
    store.collect(_trace(trace_id="b"))
    time.sleep(0.01)
    store.collect(_trace(trace_id="c"))

    ordered = [row["trace_id"] for row in store.list_recent("query", limit=10)]

    assert ordered == ["c", "b", "a"]


def test_list_recent_filters_by_trace_type(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "filter.db", auto_purge=False)
    store.collect(_trace(trace_id="q1", trace_type="query"))
    store.collect(_trace(trace_id="i1", trace_type="ingestion"))

    queries = {row["trace_id"] for row in store.list_recent("query")}
    ingestions = {row["trace_id"] for row in store.list_recent("ingestion")}

    assert queries == {"q1"}
    assert ingestions == {"i1"}


def test_list_recent_limit_rejects_out_of_range(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "limit.db", auto_purge=False)
    for value in (0, -1, 100000, True):
        with pytest.raises(TraceStoreError):
            store.list_recent("query", limit=value)


# --- check_writable -------------------------------------------------------

def test_check_writable_true_when_db_open(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "ok.db", auto_purge=False)
    assert store.check_writable() is True


def test_check_writable_false_when_sqlite_connect_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the underlying ``sqlite3.connect`` cannot open the DB, the
    probe must surface that as False (readiness: trace_store: unwritable)
    rather than raising back to callers.
    """
    import sqlite3 as sqlite_module

    store = SQLiteTraceStore(tmp_path / "down.db", auto_purge=False)

    def _boom(*args: object, **kwargs: object) -> None:
        raise sqlite_module.OperationalError("cannot open")

    monkeypatch.setattr("src.core.trace.sqlite_trace_store.sqlite3.connect", _boom)
    assert store.check_writable() is False


# --- Separate-path guard -------------------------------------------------

def test_trace_db_must_differ_from_integrity_db(tmp_path: Path) -> None:
    same = tmp_path / "shared.db"
    with pytest.raises(TraceStoreError, match="must differ"):
        SQLiteTraceStore(
            same,
            ingest_integrity_path=same,
            auto_purge=False,
        )


# --- 100-thread concurrent insert ---------------------------------------

def test_one_hundred_threads_writes_one_hundred_unique_rows(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "concurrent.db", auto_purge=False)
    rows_per_thread = 1
    thread_count = 100
    total_rows = thread_count * rows_per_thread

    barrier = threading.Barrier(thread_count)
    errors: list[str] = []

    def _worker(index: int) -> None:
        barrier.wait(timeout=10)
        try:
            store.collect(_trace(trace_id=f"trace-{index:03d}"))
        except Exception as exc:
            errors.append(f"{index}: {exc!r}")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"concurrent writers hit errors: {errors[:3]}"
    rows = store.list_recent("query", limit=total_rows + 10)
    assert len(rows) == total_rows
    assert {row["trace_id"] for row in rows} == {
        f"trace-{index:03d}" for index in range(thread_count)
    }
