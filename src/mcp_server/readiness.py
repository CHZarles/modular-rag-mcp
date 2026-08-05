"""Readiness probe for the MCP server (plan §5.7 / §C4).

The probe answers two HTTP endpoints:

* ``GET /health/live`` — liveness only. Always 200, never builds anything.
* ``GET /health/ready`` — readiness. Runs three local probes:

  - ``settings``: parses the configured YAML without contacting providers.
  - ``knowledge_store``: probes local file/path writability — never calls
    LLM / Embedding / vector search.
  - ``trace_store``: ``SQLiteTraceStore.check_writable()`` round-trip.

  All-OK → ``status: ready`` (HTTP 200). ``trace_store`` only failing →
  ``status: degraded`` (HTTP 200). ``settings`` or ``knowledge_store``
  failing → ``status: unavailable`` (HTTP 503). Wire output never
  includes paths or exception text per §6.7.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from src.core.settings import Settings, load_settings
from src.core.types import JsonDict
from src.observability.logger import get_logger

logger = get_logger(__name__)

STATUS_OK: Final = "ok"
STATUS_READY: Final = "ready"
STATUS_DEGRADED: Final = "degraded"
STATUS_UNAVAILABLE: Final = "unavailable"

# Stable codes the wire surface uses — operators and dashboards alert on
# these strings without having to parse free-form error text.
_CHECK_OK: Final = "ok"
_CHECK_SETTINGS_INVALID: Final = "settings_invalid"
_CHECK_STORE_UNWRITABLE: Final = "unwritable"
_CHECK_STORE_MISSING: Final = "missing"


@dataclass(frozen=True)
class ReadinessCheck:
    """One named probe result."""

    name: str
    code: str
    detail: str | None = None  # operator-facing, MUST NOT include raw exception text


@dataclass(frozen=True)
class ReadinessResult:
    """Aggregate status returned to the wire."""

    status: str
    checks: dict[str, ReadinessCheck] = field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return 200 if self.status in {STATUS_READY, STATUS_DEGRADED} else 503

    def to_wire(self) -> JsonDict:
        """Return the JSON body exposed on ``/health/ready`` (plan §5.7)."""
        return {
            "status": self.status,
            "checks": {name: check.code for name, check in self.checks.items()},
        }


SettingsProbe = Callable[[], ReadinessCheck]
StoreProbe = Callable[[Settings], ReadinessCheck]


class ReadinessService:
    """Run the §5.7 readiness probes without triggering external requests."""

    def __init__(
        self,
        *,
        settings_path: str | Path,
        settings_probe: SettingsProbe | None = None,
        knowledge_store_probe: StoreProbe | None = None,
        trace_store_probe: StoreProbe | None = None,
    ) -> None:
        self._settings_path = Path(settings_path).expanduser()
        self._settings_probe = settings_probe or _default_settings_probe
        self._knowledge_store_probe = (
            knowledge_store_probe or _default_knowledge_store_probe
        )
        self._trace_store_probe = trace_store_probe or _default_trace_store_probe

    def check(self) -> ReadinessResult:
        """Run all probes and aggregate into the §5.7 wire payload."""
        settings_check = self._settings_probe()
        checks: dict[str, ReadinessCheck] = {"settings": settings_check}

        if settings_check.code != _CHECK_OK:
            # Settings are required for the other probes — short-circuit.
            return ReadinessResult(status=STATUS_UNAVAILABLE, checks=checks)

        try:
            settings = load_settings(str(self._settings_path))
        except Exception:  # noqa: BLE001
            logger.exception("readiness: settings reload failed")
            checks["settings"] = ReadinessCheck(
                "settings", _CHECK_SETTINGS_INVALID
            )
            return ReadinessResult(status=STATUS_UNAVAILABLE, checks=checks)

        checks["knowledge_store"] = self._knowledge_store_probe(settings)
        checks["trace_store"] = self._trace_store_probe(settings)
        return _aggregate(checks)


def _aggregate(checks: Mapping[str, ReadinessCheck]) -> ReadinessResult:
    """Compute the top-level status from the per-check codes."""
    settings_code = checks["settings"].code
    knowledge_code = checks["knowledge_store"].code
    trace_code = checks["trace_store"].code

    if settings_code != _CHECK_OK or knowledge_code != _CHECK_OK:
        return ReadinessResult(status=STATUS_UNAVAILABLE, checks=dict(checks))
    if trace_code != _CHECK_OK:
        return ReadinessResult(status=STATUS_DEGRADED, checks=dict(checks))
    return ReadinessResult(status=STATUS_READY, checks=dict(checks))


# --- Default probes --------------------------------------------------------


def _default_settings_probe() -> ReadinessCheck:
    """Default settings probe — always OK.

    The real validation (file existence + YAML parse + section checks)
    happens inside :meth:`ReadinessService.check` so it has access to the
    configured settings path. This stub exists only so callers can
    override the probe with one that returns richer detail without
    having to re-implement the whole pipeline.
    """
    return ReadinessCheck("settings", _CHECK_OK)


def _default_knowledge_store_probe(settings: Settings) -> ReadinessCheck:
    """Verify configured local store paths are creatable without contacting providers.

    The plan forbids LLM/Embedding network calls during readiness. We
    only walk the YAML and ensure each configured on-disk path's parent
    directory exists or can be created. Vector / BM25 stores that talk
    to a remote backend get reported as ``ok`` when their config block
    is well-formed — the read path will surface real failures.
    """
    issues: list[str] = []
    targets = (
        ("vector_store", "persist_path"),
        ("ingestion", "integrity_db_path"),
        ("ingestion", "bm25_path"),
        ("ingestion", "image_db_path"),
    )
    for section_name, key in targets:
        section = _get_section(settings, section_name)
        raw = section.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            issues.append(f"{section_name}.{key}")
            continue
        if not _ensure_creatable(Path(raw)):
            issues.append(f"{section_name}.{key}")
    if issues:
        return ReadinessCheck(
            "knowledge_store",
            _CHECK_STORE_UNWRITABLE,
            detail="; ".join(issues),
        )
    return ReadinessCheck("knowledge_store", _CHECK_OK)


def _default_trace_store_probe(settings: Settings) -> ReadinessCheck:
    """Open a SQLiteTraceStore against the configured path and probe it."""
    raw = settings.observability.get("trace_db_path")
    if not isinstance(raw, str) or not raw.strip():
        path = Path("./data/db/traces.db")
    else:
        path = Path(raw).expanduser()
    try:
        from src.core.trace import SQLiteTraceStore

        store = SQLiteTraceStore(path, auto_purge=False)
    except FileNotFoundError:
        return ReadinessCheck(
            "trace_store", _CHECK_STORE_MISSING, detail="trace_db_path"
        )
    except Exception:  # noqa: BLE001
        logger.exception("readiness: trace store open failed")
        return ReadinessCheck("trace_store", _CHECK_STORE_UNWRITABLE)
    if not store.check_writable():
        return ReadinessCheck("trace_store", _CHECK_STORE_UNWRITABLE)
    return ReadinessCheck("trace_store", _CHECK_OK)


def _get_section(settings: Settings, name: str) -> dict[str, Any]:
    section = getattr(settings, name, None)
    if isinstance(section, dict):
        return section
    return {}


def _ensure_creatable(path: Path) -> bool:
    """Return True when ``path`` already exists or its parent is writable."""
    if path.exists():
        return True
    parent = path.parent
    if parent.exists():
        return parent.is_dir() and _is_writable(parent)
    return _is_writable(parent)


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readiness_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "ReadinessCheck",
    "ReadinessResult",
    "ReadinessService",
    "SettingsProbe",
    "StoreProbe",
    "STATUS_DEGRADED",
    "STATUS_READY",
    "STATUS_UNAVAILABLE",
]
