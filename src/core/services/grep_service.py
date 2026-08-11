"""Literal grep orchestration over the independent SQLite index."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from src.core.types import JsonDict
from src.ingestion.storage.sqlite_grep_index import (
    GrepQueryTimeoutError,
    SQLiteGrepIndex,
    normalize_search_text,
)
from src.ports.ingestion import FileIntegrityStore, GenerationStateStore


@dataclass(frozen=True)
class GrepMatch:
    chunk_id: str
    text: str
    source_path: str
    page: int | None
    metadata: JsonDict
    match_count: int


@dataclass(frozen=True)
class GrepResponse:
    matches: list[GrepMatch]
    truncated: bool = False
    timed_out: bool = False
    trace_id: str | None = None


class GrepService:
    def __init__(
        self,
        index: SQLiteGrepIndex,
        generation_store: GenerationStateStore,
    ) -> None:
        self.index = index
        self.generation_store = generation_store

    def search(
        self,
        *,
        pattern: str,
        collection: str = "default",
        top_k: int = 20,
        case_sensitive: bool = False,
    ) -> GrepResponse:
        deadline = time.monotonic() + self.index.timeout_ms / 1000.0
        active_generations = self.generation_store.get_active_generations(collection)
        if not active_generations:
            return GrepResponse(matches=[])
        if time.monotonic() >= deadline:
            return GrepResponse(matches=[], truncated=True, timed_out=True)

        needle = pattern if case_sensitive else normalize_search_text(pattern)
        column: Literal["text", "search_text"] = (
            "text" if case_sensitive else "search_text"
        )
        matches: list[GrepMatch] = []
        timed_out = False
        truncated = False
        try:
            for candidate in self.index.iter_candidates(
                collection=collection,
                pattern=needle,
                column=column,
                active_generations=active_generations,
                deadline=deadline,
            ):
                haystack = (
                    candidate.text
                    if case_sensitive
                    else normalize_search_text(candidate.text)
                )
                match_count = haystack.count(needle)
                if match_count:
                    matches.append(
                        GrepMatch(
                            chunk_id=candidate.chunk_id,
                            text=candidate.text,
                            source_path=candidate.source_path,
                            page=candidate.page,
                            metadata=candidate.metadata,
                            match_count=match_count,
                        )
                    )
                    if len(matches) > top_k:
                        truncated = True
                        break
                if time.monotonic() >= deadline:
                    timed_out = True
                    truncated = True
                    break
        except GrepQueryTimeoutError:
            timed_out = True
            truncated = True

        return GrepResponse(
            matches=matches[:top_k],
            truncated=truncated,
            timed_out=timed_out,
        )


def active_generation_counts(
    integrity: FileIntegrityStore,
    collection: str | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return one FileIntegrity snapshot and its published Chunk counts."""
    active = integrity.get_active_generations(collection)
    counts: dict[str, int] = {}
    for row in integrity.list_processed(collection):
        doc_key = row.get("doc_key")
        generation = row.get("generation")
        chunk_count = row.get("chunk_count")
        if (
            row.get("attempt_status") == "published"
            and isinstance(doc_key, str)
            and active.get(doc_key) == generation
            and isinstance(chunk_count, int)
            and not isinstance(chunk_count, bool)
            and chunk_count >= 0
        ):
            counts[doc_key] = chunk_count
    return active, counts


__all__ = ["GrepMatch", "GrepResponse", "GrepService", "active_generation_counts"]
