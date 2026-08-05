"""SQLite-backed Trace store — single source of truth for plan §C2.2.

WAL mode + 5 s busy timeout + one connection per operation keep the design
safe under the kind of burst that exists between the MCP HTTP front door and
the private Dashboard. Concurrent writers (plan §12) get a fair queue and
the C2.2 acceptance proof is "100 threads × 100 distinct rows, no lock
error, no lost row".

Wiring notes:
* The schema is created idempotently at first open (``PRAGMA user_version=1``
  + ``CREATE ... IF NOT EXISTS``). Plan §6.4 explicitly forbids migrations
  this cycle; future schema changes must extend ``user_version`` first.
* Every public method opens its own short-lived SQLite connection. Sharing
  would force threads to coordinate handles and gain nothing on a single
  host (WAL already multiplexes writers).
* ``collect()`` validates everything *before* opening the write transaction
  so a malformed trace never holds a lock.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Final, Literal

from src.core.trace.trace_context import TraceContext
from src.core.types import JsonDict
from src.observability.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_VERSION: Final = 1
_MAX_LIMIT: Final = 1000
_DETAIL_MODES: Final = frozenset({"compact", "debug"})
_AUTO_PURGE_INTERVAL: Final = timedelta(days=1)

TraceDetail = Literal["compact", "debug"]


class TraceStoreError(ValueError):
    """Raised when input fails the SQLite-backed trace contract."""


@dataclass(frozen=True)
class RetentionPolicy:
    """Configured retention window; ``None`` disables automatic expiry."""

    days: int | None

    def __post_init__(self) -> None:
        if self.days is not None and not (1 <= self.days <= 3650):
            raise TraceStoreError(
                f"retention_days must be between 1 and 3650, got {self.days}"
            )

    @property
    def enabled(self) -> bool:
        return self.days is not None


class SQLiteTraceStore:
    """Concurrent-safe Trace sink backed by a single SQLite database file."""

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int | None = 90,
        detail: TraceDetail = "compact",
        ingest_integrity_path: str | Path | None = None,
        auto_purge: bool = True,
    ) -> None:
        self._path = Path(path)
        self._retention = RetentionPolicy(days=retention_days)
        self._detail: TraceDetail = detail  # type: ignore[assignment]
        if self._detail not in _DETAIL_MODES:
            raise TraceStoreError(
                f"detail must be one of {sorted(_DETAIL_MODES)}, got {detail!r}"
            )
        if ingest_integrity_path is not None:
            self._enforce_distinct_path(Path(ingest_integrity_path))
        self._auto_purge = auto_purge
        self._auto_purge_lock = Lock()
        self._auto_purge_last_run_at: datetime | None = None
        self._bootstrap()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def retention(self) -> RetentionPolicy:
        return self._retention

    @property
    def detail(self) -> TraceDetail:
        return self._detail

    # --- mutation ----------------------------------------------------------

    def collect(self, trace: TraceContext) -> None:
        """Validate, finish, and insert one trace record.

        Same ``trace_id`` repeated must fail with :class:`TraceStoreError` —
        plan §6.5 forbids silent overwrite of audit records.
        """
        trace.finish()
        payload = _serialise_trace(trace, detail=self._detail)
        _validate_payload(payload)

        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO trace_event ("
                    "trace_id, schema_version, trace_type, request_id, actor_key,"
                    " session_id, started_at, finished_at, total_elapsed_ms,"
                    " status, error_code, query_text, query_normalized,"
                    " collection, top_k, result_count, zero_result, payload_json"
                    ") VALUES ("
                    ":trace_id, :schema_version, :trace_type, :request_id, :actor_key,"
                    " :session_id, :started_at, :finished_at, :total_elapsed_ms,"
                    " :status, :error_code, :query_text, :query_normalized,"
                    " :collection, :top_k, :result_count, :zero_result, :payload_json"
                    ")",
                    _payload_bindings(payload),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                message = str(exc).upper()
                if "TRACE_ID" in message or "PRIMARY KEY" in message or "UNIQUE" in message:
                    raise TraceStoreError(
                        f"trace_id already persisted: {trace.trace_id}"
                    ) from exc
                raise TraceStoreError(str(exc)) from exc
            except sqlite3.Error as exc:
                conn.rollback()
                raise TraceStoreError(f"trace insert failed: {exc}") from exc

    def purge(self, *, before: datetime | None = None, actor_key: str | None = None) -> int:
        """Delete rows matching ``before`` / ``actor_key`` (AND, both optional)."""
        if before is None and actor_key is None:
            raise TraceStoreError(
                "purge requires at least one of --before or --actor-key"
            )
        clauses: list[str] = []
        params: list[object] = []
        if before is not None:
            if before.tzinfo is None:
                before = before.replace(tzinfo=timezone.utc)
            clauses.append("started_at < ?")
            params.append(before.isoformat())
        if actor_key is not None:
            cleaned = actor_key.strip()
            if not cleaned:
                raise TraceStoreError("actor_key must not be blank")
            clauses.append("actor_key = ?")
            params.append(cleaned)
        sql = f"DELETE FROM trace_event WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.rowcount or 0)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete traces older than the configured retention window."""
        if not self._retention.enabled:
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(
            days=self._retention.days  # type: ignore[arg-type]
        )
        return self.purge(before=cutoff)

    # --- read --------------------------------------------------------------

    def list_recent(self, trace_type: str, limit: int = 200) -> list[JsonDict]:
        """Return up to ``limit`` rows of ``trace_type``, newest first."""
        if not isinstance(trace_type, str) or not trace_type.strip():
            raise TraceStoreError("trace_type must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TraceStoreError("limit must be a positive integer")
        if limit < 1 or limit > _MAX_LIMIT:
            raise TraceStoreError(
                f"limit must be between 1 and {_MAX_LIMIT}, got {limit}"
            )
        sql = (
            "SELECT payload_json FROM trace_event "
            "WHERE trace_type = ? "
            "ORDER BY started_at DESC, trace_id DESC "
            "LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (trace_type.strip(), limit)).fetchall()
        return [_decode_payload(row[0]) for row in rows]

    # --- health ------------------------------------------------------------

    def check_writable(self) -> bool:
        """Confirm the store accepts writes without mutating any trace row.

        Probe via a single ``BEGIN IMMEDIATE`` transaction, a no-op
        ``UPDATE 0``, then ``ROLLBACK``. Returns False on any sqlite3 error
        so the readiness endpoint can light up ``trace_store: unwritable``
        (plan §5.7) without leaking internals.
        """
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE trace_event SET status = status WHERE 0"
                )
                conn.execute("ROLLBACK")
            return True
        except sqlite3.Error:
            return False

    # --- housekeeping ------------------------------------------------------

    def maybe_auto_purge(self, now: datetime | None = None) -> int:
        """Run ``purge_expired`` at most once per 24h per process."""
        if not self._auto_purge or not self._retention.enabled:
            return 0
        now = now or datetime.now(timezone.utc)
        with self._auto_purge_lock:
            last = self._auto_purge_last_run_at
            if last is not None and (now - last) < _AUTO_PURGE_INTERVAL:
                return 0
            deleted = self.purge_expired(now=now)
            self._auto_purge_last_run_at = now
            return deleted

    # --- internals ---------------------------------------------------------

    def _bootstrap(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current_version > _SCHEMA_VERSION:
                raise TraceStoreError(
                    f"trace DB schema newer than supported ({current_version} > {_SCHEMA_VERSION})"
                )
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # ``check_same_thread=False`` is paired with the "open per call"
        # discipline above; concurrent calls do not share a handle.
        connection = sqlite3.connect(
            self._path, timeout=5.0, isolation_level=None, check_same_thread=False
        )
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()

    def _enforce_distinct_path(self, other: Path) -> None:
        same = self._path.resolve() == other.resolve()
        if same:
            raise TraceStoreError(
                f"trace DB path must differ from integrity DB path: {self._path}"
            )


def _serialise_trace(trace: TraceContext, *, detail: TraceDetail) -> JsonDict:
    payload = trace.to_dict()
    payload["schema_version"] = _SCHEMA_VERSION
    payload["detail"] = detail
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise TraceStoreError("trace metadata must be an object")
    status = metadata.get("status")
    if status is not None and not isinstance(status, str):
        raise TraceStoreError("metadata.status must be a string when present")
    return payload


def _validate_payload(payload: JsonDict) -> None:
    """Reject malformed traces before opening a write transaction."""
    _require_non_negative(payload.get("total_elapsed_ms"), "total_elapsed_ms")
    _require_non_empty_str(payload.get("trace_id"), "trace_id")
    _require_non_empty_str(payload.get("trace_type"), "trace_type")
    _require_non_empty_str(payload.get("started_at"), "started_at")
    _require_text_when_present(payload.get("finished_at"), "finished_at")
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TraceStoreError(f"trace payload is not JSON-serialisable: {exc}") from exc


def _payload_bindings(payload: JsonDict) -> JsonDict:
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise TraceStoreError("metadata must be an object")
    return {
        "trace_id": payload["trace_id"],
        "schema_version": int(payload.get("schema_version", _SCHEMA_VERSION)),
        "trace_type": payload["trace_type"],
        "request_id": metadata.get("request_id"),
        "actor_key": metadata.get("actor_key"),
        "session_id": metadata.get("session_id"),
        "started_at": payload["started_at"],
        "finished_at": payload.get("finished_at"),
        "total_elapsed_ms": float(payload["total_elapsed_ms"]),
        "status": _status(metadata),
        "error_code": metadata.get("error_code"),
        "query_text": metadata.get("query"),
        "query_normalized": metadata.get("query_normalized"),
        "collection": metadata.get("collection"),
        "top_k": metadata.get("top_k"),
        "result_count": metadata.get("result_count"),
        "zero_result": _bool_int(metadata.get("zero_result")),
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def _status(metadata: JsonDict) -> str:
    status = metadata.get("status")
    return status if isinstance(status, str) and status else "unknown"


def _bool_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        return None
    return 1 if value else 0


def _require_non_empty_str(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TraceStoreError(f"trace field must be a non-empty string: {field}")


def _require_text_when_present(value: object, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TraceStoreError(f"trace field must be a string when present: {field}")


def _require_non_negative(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceStoreError(f"trace field must be a number: {field}")
    parsed = float(value)
    if not _is_finite(parsed) or parsed < 0:
        raise TraceStoreError(
            f"trace field must be finite and non-negative: {field}"
        )


def _is_finite(value: float) -> bool:
    return value != float("inf") and value != float("-inf") and value == value


def _decode_payload(raw: object) -> JsonDict:
    if not isinstance(raw, str):
        raise TraceStoreError("stored payload must be a JSON string")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TraceStoreError(f"stored payload is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise TraceStoreError("stored payload must decode to an object")
    return decoded


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trace_event (
    trace_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    trace_type TEXT NOT NULL,
    request_id TEXT,
    actor_key TEXT,
    session_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_elapsed_ms REAL NOT NULL CHECK (total_elapsed_ms >= 0),
    status TEXT NOT NULL,
    error_code TEXT,
    query_text TEXT,
    query_normalized TEXT,
    collection TEXT,
    top_k INTEGER CHECK (top_k IS NULL OR top_k > 0),
    result_count INTEGER CHECK (result_count IS NULL OR result_count >= 0),
    zero_result INTEGER CHECK (zero_result IS NULL OR zero_result IN (0, 1)),
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_type_started
    ON trace_event(trace_type, started_at DESC, trace_id DESC);
CREATE INDEX IF NOT EXISTS idx_trace_started
    ON trace_event(started_at);
CREATE INDEX IF NOT EXISTS idx_trace_actor_started
    ON trace_event(actor_key, started_at DESC, trace_id DESC);
CREATE INDEX IF NOT EXISTS idx_trace_query_started
    ON trace_event(query_normalized, started_at DESC, trace_id DESC);
"""


__all__ = [
    "RetentionPolicy",
    "SQLiteTraceStore",
    "TraceDetail",
    "TraceStoreError",
]
