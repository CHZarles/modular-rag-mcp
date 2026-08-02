"""Dashboard-facing view of runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.settings import ConfigSection, Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


@dataclass(frozen=True)
class ComponentSummary:
    """Safe, display-ready configuration for one pluggable component."""

    code: str
    label: str
    provider: str
    model: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    @property
    def enabled(self) -> bool:
        return self.provider.lower() not in {"none", "disabled", "off"}


@dataclass(frozen=True)
class DashboardOptions:
    """Validated local server options used by the dashboard launcher."""

    enabled: bool
    address: str
    port: int
    traces_dir: Path
    auto_refresh: bool
    refresh_interval: int


class ConfigService:
    """Load Settings once and expose only values suitable for the dashboard."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def from_path(cls, path: str | Path = DEFAULT_SETTINGS_PATH) -> ConfigService:
        return cls(load_settings(str(Path(path).expanduser())))

    def component_summaries(self) -> tuple[ComponentSummary, ...]:
        settings = self.settings
        return (
            ComponentSummary(
                code="GEN",
                label="LLM",
                provider=_text(settings.llm, "provider"),
                model=_optional_text(settings.llm, "model"),
            ),
            ComponentSummary(
                code="EMB",
                label="Embedding",
                provider=_text(settings.embedding, "provider"),
                model=_optional_text(settings.embedding, "model"),
                details=(("Dimension", _display(settings.embedding.get("dimension"), "Auto")),),
            ),
            ComponentSummary(
                code="SPLIT",
                label="Splitter",
                provider=_text(settings.splitter, "provider"),
                details=(
                    ("Chunk size", _display(settings.splitter.get("chunk_size"))),
                    ("Overlap", _display(settings.splitter.get("chunk_overlap"))),
                ),
            ),
            ComponentSummary(
                code="RANK",
                label="Reranker",
                provider=_text(settings.rerank, "backend"),
                model=_optional_text(settings.rerank, "model"),
                details=(("Top M", _display(settings.rerank.get("top_m"))),),
            ),
            ComponentSummary(
                code="STORE",
                label="Vector store",
                provider=_text(settings.vector_store, "backend"),
                model=_optional_text(settings.vector_store, "collection_name"),
                details=(("Distance", _display(settings.vector_store.get("distance_metric"))),),
            ),
            ComponentSummary(
                code="EVAL",
                label="Evaluator",
                provider=_backend_list(settings.evaluation.get("backends")),
                details=(("Golden set", _path_name(settings.evaluation.get("golden_test_set"))),),
            ),
        )

    def dashboard_options(self) -> DashboardOptions:
        config = self.settings.dashboard
        return DashboardOptions(
            enabled=_boolean(config.get("enabled"), default=True),
            address=_text(config, "address", default="127.0.0.1"),
            port=_bounded_int(config.get("port"), default=8501, minimum=1, maximum=65535),
            traces_dir=_project_path(config.get("traces_dir"), default="./logs"),
            auto_refresh=_boolean(config.get("auto_refresh"), default=True),
            refresh_interval=_bounded_int(
                config.get("refresh_interval"),
                default=5,
                minimum=1,
                maximum=3600,
            ),
        )

    def trace_path(self) -> Path:
        """Return the same configured JSONL path used by trace producers."""
        return _project_path(
            self.settings.observability.get("log_file"),
            default="./logs/traces.jsonl",
        )


def _text(config: ConfigSection, key: str, default: str = "Not configured") -> str:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _optional_text(config: ConfigSection, key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _display(value: Any, default: str = "Not configured") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _backend_list(value: Any) -> str:
    if isinstance(value, list):
        backends = [str(item).strip() for item in value if str(item).strip()]
        if backends:
            return ", ".join(backends)
    return "none"


def _path_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "Not configured"
    return Path(value).name


def _boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"dashboard configuration error: invalid boolean {value!r}")


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("dashboard configuration error: expected an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("dashboard configuration error: expected an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"dashboard configuration error: integer must be between {minimum} and {maximum}"
        )
    return parsed


def _project_path(value: Any, *, default: str) -> Path:
    raw = str(value if value not in (None, "") else default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


__all__ = [
    "ComponentSummary",
    "ConfigService",
    "DEFAULT_SETTINGS_PATH",
    "DashboardOptions",
]
