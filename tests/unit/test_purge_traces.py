"""Tests for ``scripts/purge_traces.py`` (plan §C2.4 / §6.6).

Covers the CLI surface (exit codes, filter combinations) and the privacy
guarantee that the operator-facing output never includes the raw query
text. The store is exercised via a temp ``SQLiteTraceStore`` so the CLI
does not need a real settings.yaml.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import purge_traces
from src.core.trace import SQLiteTraceStore, TraceContext


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteTraceStore]:
    yield SQLiteTraceStore(tmp_path / "traces.db", auto_purge=False)


def _trace(
    *,
    trace_type: str,
    trace_id: str,
    started_at: datetime,
    actor_key: str | None,
    query: str = "raw-customer-question",
) -> TraceContext:
    trace = TraceContext(trace_type=trace_type)
    trace.trace_id = trace_id
    trace.started_at = started_at.isoformat()
    trace.metadata.update(
        {
            "status": "success",
            "query": query,
            "query_normalized": query.lower(),
            "actor_key": actor_key,
        }
    )
    trace.record_stage(
        "execute",
        {"method": trace_type, "provider": "local"},
        elapsed_ms=1.0,
    )
    trace._finish_mono = trace._start_mono + 0.001  # type: ignore[attr-defined]
    trace.finished_at = (
        datetime.fromisoformat(trace.started_at) + timedelta(milliseconds=1)
    ).isoformat()
    return trace


def _seed(store: SQLiteTraceStore) -> datetime:
    base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    rows = [
        _trace(trace_type="query", trace_id="q-old-actor1", started_at=base, actor_key="actor-1"),
        _trace(trace_type="query", trace_id="q-new-actor1", started_at=base + timedelta(days=5), actor_key="actor-1"),
        _trace(trace_type="ingestion", trace_id="i-old-actor2", started_at=base, actor_key="actor-2"),
        _trace(trace_type="ingestion", trace_id="i-new-actor2", started_at=base + timedelta(days=5), actor_key="actor-2"),
    ]
    for trace in rows:
        store.collect(trace)
    return base


def _capture(
    func,
    *args: object,
    **kwargs: object,
) -> tuple[int, str, str]:
    """Run ``func``, swallowing ``SystemExit`` so argparse error paths are testable."""
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = func(*args, **kwargs)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


# --- CLI surface -----------------------------------------------------------


def test_cli_requires_at_least_one_filter() -> None:
    code, _stdout, stderr = _capture(purge_traces.main, [])
    assert code != 0
    assert "--before" in stderr and "--actor-key" in stderr


def test_cli_uses_provided_db_path_without_loading_settings(
    store: SQLiteTraceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(store)
    monkeypatch.setattr(
        purge_traces,
        "resolve_settings_path",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    monkeypatch.setattr(
        purge_traces,
        "load_settings",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    code, stdout, stderr = _capture(
        purge_traces.main,
        [
            "--actor-key", "actor-1",
            "--db", str(store.path),
        ],
    )
    assert code == 0
    assert stderr == ""
    summary = json.loads(stdout)
    assert summary["actor_key"] == "actor-1"
    assert summary["matched"] == 2
    assert summary["deleted"] == 2
    # The two actor-1 rows are gone, the actor-2 rows survive.
    assert {row["trace_id"] for row in store.list_recent("query", limit=10)} == set()
    assert {row["trace_id"] for row in store.list_recent("ingestion", limit=10)} == {"i-old-actor2", "i-new-actor2"}


def test_cli_purges_by_before_timestamp(
    store: SQLiteTraceStore, tmp_path: Path
) -> None:
    _seed(store)
    cutoff = datetime(2026, 8, 4, tzinfo=timezone.utc)

    code, stdout, _stderr = _capture(
        purge_traces.main,
        ["--before", cutoff.isoformat(), "--db", str(store.path)],
    )

    assert code == 0
    summary = json.loads(stdout)
    assert summary["matched"] == 2
    assert summary["deleted"] == 2
    remaining = (
        {row["trace_id"] for row in store.list_recent("query", limit=10)}
        | {row["trace_id"] for row in store.list_recent("ingestion", limit=10)}
    )
    assert remaining == {"q-new-actor1", "i-new-actor2"}


def test_cli_combines_before_and_actor_key(
    store: SQLiteTraceStore, tmp_path: Path
) -> None:
    _seed(store)
    cutoff = datetime(2026, 8, 4, tzinfo=timezone.utc)

    code, stdout, _stderr = _capture(
        purge_traces.main,
        [
            "--before", cutoff.isoformat(),
            "--actor-key", "actor-2",
            "--db", str(store.path),
        ],
    )

    assert code == 0
    summary = json.loads(stdout)
    # Only the actor-2 row before the cutoff matches.
    assert summary["matched"] == 1
    assert summary["deleted"] == 1
    remaining = (
        {row["trace_id"] for row in store.list_recent("query", limit=10)}
        | {row["trace_id"] for row in store.list_recent("ingestion", limit=10)}
    )
    assert remaining == {"q-old-actor1", "q-new-actor1", "i-new-actor2"}


def test_cli_dry_run_reports_match_without_deleting(
    store: SQLiteTraceStore,
) -> None:
    _seed(store)

    code, stdout, _stderr = _capture(
        purge_traces.main,
        ["--actor-key", "actor-1", "--db", str(store.path), "--dry-run"],
    )

    assert code == 0
    summary = json.loads(stdout)
    assert summary["dry_run"] is True
    assert summary["matched"] == 2
    assert summary["deleted"] == 0
    # All rows still present.
    remaining = (
        {row["trace_id"] for row in store.list_recent("query", limit=10)}
        | {row["trace_id"] for row in store.list_recent("ingestion", limit=10)}
    )
    assert len(remaining) == 4


def test_cli_rejects_blank_actor_key(store: SQLiteTraceStore) -> None:
    code, _stdout, stderr = _capture(
        purge_traces.main,
        ["--actor-key", "   ", "--db", str(store.path)],
    )

    # Blank actor_key falls back to "no filter" → CLI rejects.
    assert code != 0
    assert "--before" in stderr


def test_cli_output_never_includes_raw_query(store: SQLiteTraceStore) -> None:
    """Privacy §6.7: operator-facing output must not echo query text."""
    _seed(store)
    captured: dict[str, str] = {}

    for argv in (
        ["--actor-key", "actor-1", "--db", str(store.path)],
        ["--before", "2026-08-04T00:00:00+00:00", "--db", str(store.path)],
    ):
        code, stdout, stderr = _capture(purge_traces.main, argv)
        assert code == 0
        captured["stdout"] = captured.get("stdout", "") + stdout
        captured["stderr"] = captured.get("stderr", "") + stderr

    # Both stdout and stderr must be free of customer query text.
    for needle in ("raw-customer-question", "query_normalized"):
        assert needle not in captured["stdout"]
        assert needle not in captured["stderr"]


def test_cli_reports_unwritable_store(tmp_path: Path) -> None:
    # Build a store, close it, then make the path read-only so check_writable fails.
    db_path = tmp_path / "ro.db"
    SQLiteTraceStore(db_path, auto_purge=False)
    db_path.chmod(0o400)

    try:
        code, _stdout, stderr = _capture(
            purge_traces.main,
            ["--actor-key", "actor-1", "--db", str(db_path)],
        )
        assert code == purge_traces._PURGE_RUNTIME
        assert "unable to open trace store" in stderr
    finally:
        # pytest cleanup needs write access; relax before tmp_path teardown.
        db_path.chmod(0o644)


def test_parse_iso_datetime_accepts_z_suffix() -> None:
    parsed = purge_traces._parse_iso_datetime("2026-08-01T00:00:00Z")
    assert parsed.tzinfo is not None
    assert parsed.year == 2026 and parsed.month == 8 and parsed.day == 1
