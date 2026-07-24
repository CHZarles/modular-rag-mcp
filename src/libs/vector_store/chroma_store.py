"""ChromaStore: minimal in-process vector store with local persistence.

Implements :class:`BaseVectorStore` against a JSON-on-disk format under
``config.persist_path`` (default ``./data/db/chroma``). This is the
"clean-start" replacement for the upstream ``chromadb`` library — it
honours the same contract so the rest of the system (vector upserter,
retrievers) can be exercised end-to-end without taking on chromadb's
heavy transitive deps.

Operations:
- ``upsert``: append-or-replace by id, persist to disk atomically.
- ``query``: cosine similarity over dense vectors, with simple metadata
  equality filters.
- ``get_by_ids``: return records matching a list of ids.
- ``delete_by_metadata``: drop records whose metadata matches every
  key/value pair in the filter.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from src.core.settings import VectorStoreConfig
from src.core.types import ChunkRecord, SearchHit
from src.ports.ingestion import BaseVectorStore

_PERSIST_FILE = "chroma_store.json"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ChromaStore(BaseVectorStore):
    """Local JSON-backed vector store implementing ``BaseVectorStore``."""

    def __init__(self, config: VectorStoreConfig) -> None:
        self.config = config
        self.persist_path = Path(config.persist_path).expanduser().resolve()
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, ChunkRecord] = {}
        self._load()

    # ------------------------------------------------------------------
    # BaseVectorStore
    # ------------------------------------------------------------------

    def upsert(
        self,
        records: list[ChunkRecord],
        trace: Any | None = None,
    ) -> None:
        for r in records:
            self._records[r.id] = r
        self._save()

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        scored: list[tuple[float, ChunkRecord]] = []
        for rec in self._records.values():
            if filters and not self._matches_filter(rec, filters):
                continue
            if not rec.dense_vector:
                continue
            scored.append((_cosine(vector, rec.dense_vector), rec))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            SearchHit(
                id=rec.id,
                text=rec.text,
                metadata=dict(rec.metadata),
                score=score,
                score_kind="similarity",
            )
            for score, rec in scored[:top_k]
        ]

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return [self._records[i] for i in ids if i in self._records]

    def delete_by_metadata(self, filters: dict) -> int:
        to_delete = [rid for rid, rec in self._records.items() if self._matches_filter(rec, filters)]
        for rid in to_delete:
            del self._records[rid]
        if to_delete:
            self._save()
        return len(to_delete)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        path = self.persist_path / _PERSIST_FILE
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return  # corrupt file → treat as empty; safer than crashing
        self._records = {
            item["id"]: ChunkRecord.from_dict(item) for item in payload.get("records", [])
        }

    def _save(self) -> None:
        """Atomic write: write to a tmp file then rename."""
        path = self.persist_path / _PERSIST_FILE
        tmp_dir = tempfile.mkdtemp(dir=self.persist_path)
        try:
            tmp_path = Path(tmp_dir) / _PERSIST_FILE
            tmp_path.write_text(
                json.dumps(
                    {"records": [r.to_dict() for r in self._records.values()]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            shutil.move(str(tmp_path), str(path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _matches_filter(rec: ChunkRecord, filters: dict) -> bool:
        for key, expected in filters.items():
            if rec.metadata.get(key) != expected:
                return False
        return True