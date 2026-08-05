"""Manual Trace purge CLI (plan §6.6).

Usage::

    python -m scripts.purge_traces --before 2026-08-01
    python -m scripts.purge_traces --actor-key alice
    python -m scripts.purge_traces --before 2026-08-01 --actor-key alice
    python -m scripts.purge_traces --settings config/settings.yaml --actor-key alice

The CLI never prints raw query text. It only echoes the filters it used,
the matching row count it observed and the number of rows it removed, so
operators can confirm the call without leaking customer questions into
terminal scrollback.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.settings import load_settings, resolve_settings_path  # noqa: E402
from src.core.trace import SQLiteTraceStore  # noqa: E402

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
_PURGE_OK = 0
_PURGE_BAD_ARGS = 2
_PURGE_RUNTIME = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purge_traces",
        description="Manually delete rows from the SQLite Trace store.",
    )
    parser.add_argument(
        "--before",
        type=_parse_iso_datetime,
        help="Delete traces whose started_at is strictly before this ISO timestamp.",
    )
    parser.add_argument(
        "--actor-key",
        type=str,
        help="Delete traces recorded under this anonymous actor key.",
    )
    parser.add_argument(
        "--settings",
        type=str,
        default=None,
        help=(
            "Optional settings.yaml path; falls back to RAG_SETTINGS_PATH "
            "and the bundled config/settings.yaml."
        ),
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help=(
            "Optional SQLite DB path override; defaults to "
            "observability.trace_db_path from the resolved settings."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the matching count and exit without deleting any rows.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    before = args.before
    actor_key = args.actor_key.strip() if isinstance(args.actor_key, str) else None
    if actor_key == "":
        actor_key = None
    if before is None and actor_key is None:
        parser.error("at least one of --before or --actor-key is required")

    # When --db is supplied we skip loading settings entirely; the CLI stays
    # usable in test sandboxes and in air-gapped recovery flows where the
    # only thing the operator wants is "delete rows from this file".
    db_path: Path | None = None
    if isinstance(args.db, str) and args.db.strip():
        db_path = Path(args.db).expanduser()
    else:
        try:
            settings_path = resolve_settings_path(args.settings, default_path=DEFAULT_SETTINGS_PATH)
            settings = load_settings(str(settings_path))
        except Exception as exc:  # noqa: BLE001
            print(f"unable to load settings: {exc}", file=sys.stderr)
            return _PURGE_RUNTIME
        db_path = _resolve_db_path(args.db, settings)
    assert db_path is not None  # for type checkers

    try:
        store = SQLiteTraceStore(db_path, auto_purge=False)
    except Exception as exc:  # noqa: BLE001
        print(f"unable to open trace store: {exc}", file=sys.stderr)
        return _PURGE_RUNTIME
    if not store.check_writable():
        print(f"trace store is not writable: {db_path}", file=sys.stderr)
        return _PURGE_RUNTIME

    match_count = _count_matches(store, before=before, actor_key=actor_key)
    summary = {
        "before": before.isoformat() if before else None,
        "actor_key": actor_key,
        "matched": match_count,
        "deleted": 0,
        "db_path": str(db_path),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return _PURGE_OK

    deleted = store.purge(before=before, actor_key=actor_key)
    summary["deleted"] = deleted
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if deleted < match_count:
        print(
            f"warning: deleted {deleted} rows but matched {match_count}; "
            "another process may have removed rows concurrently.",
            file=sys.stderr,
        )
    return _PURGE_OK


def _count_matches(
    store: SQLiteTraceStore,
    *,
    before: datetime | None,
    actor_key: str | None,
) -> int:
    """Estimate matches via list_recent over recent types."""
    total = 0
    for trace_type in ("query", "ingestion"):
        for trace in store.list_recent(trace_type, limit=_SQLITE_TRACE_LIST_LIMIT):
            if not _matches(trace, before=before, actor_key=actor_key):
                continue
            total += 1
    return total


def _matches(
    trace: dict[str, object],
    *,
    before: datetime | None,
    actor_key: str | None,
) -> bool:
    if before is not None:
        started_at = trace.get("started_at")
        if not isinstance(started_at, str):
            return False
        parsed = _parse_iso_datetime(started_at)
        if parsed >= before:
            return False
    if actor_key is not None:
        metadata = trace.get("metadata")
        if not isinstance(metadata, dict):
            return False
        if metadata.get("actor_key") != actor_key:
            return False
    return True


def _resolve_db_path(override: str | None, settings: object) -> Path:
    if isinstance(override, str) and override.strip():
        return Path(override).expanduser()
    observability = getattr(settings, "observability", {}) or {}
    raw = observability.get("trace_db_path") if isinstance(observability, dict) else None
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return Path("./data/db/traces.db")


def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive timestamps are treated as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ``SQLiteTraceStore.list_recent`` caps at 1000 rows per call — same ceiling the
# Dashboard applies. Manual purge only operates on the recent window so the
# CLI matches operator intent ("purge recent traces") without forcing them to
# also remember the retention window.
_SQLITE_TRACE_LIST_LIMIT = 1000


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
