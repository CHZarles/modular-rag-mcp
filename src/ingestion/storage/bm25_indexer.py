"""BM25 indexer — in-memory inverted index with TF-IDF-style scoring."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.core.types import Chunk, SearchHit

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_K1_DEFAULT = 1.5
_B_DEFAULT = 0.75


class BM25IndexStore:
    """Pure-Python BM25 — sufficient for tests and small indexes."""

    K1 = 1.5
    B = 0.75

    def __init__(self) -> None:
        # inverted_index[term] = list of (chunk_id, term_freq)
        self._inverted: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self._doc_lens: dict[str, int] = {}
        self._doc_count = 0
        self._avgdl = 0.0
        self._chunks: dict[str, Chunk] = {}

    # ------------------------------------------------------------------
    # Build / query
    # ------------------------------------------------------------------

    def upsert(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[dict[str, Any]] | None = None,
        trace: Any | None = None,
    ) -> None:
        for i, chunk in enumerate(chunks):
            terms = self._terms_for(chunk, sparse_vectors, i)
            self._chunks[chunk.id] = chunk
            self._doc_lens[chunk.id] = len(terms)
            for term in set(terms):
                tf = terms.count(term)
                self._inverted[term].append((chunk.id, tf))
            # Reset doc count and recompute below.
        self._doc_count = len(self._doc_lens)
        self._avgdl = (
            sum(self._doc_lens.values()) / self._doc_count if self._doc_count else 0.0
        )

    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: dict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        if not self._inverted or not keywords:
            return []
        scores: dict[str, float] = defaultdict(float)
        for term in keywords:
            postings = self._inverted.get(term.lower(), [])
            if not postings:
                continue
            idf = math.log(
                1 + (self._doc_count - len(postings) + 0.5) / (len(postings) + 0.5)
            )
            for chunk_id, tf in postings:
                if filters and not _matches(self._chunks[chunk_id].metadata, filters):
                    continue
                doc_len = self._doc_lens[chunk_id]
                numerator = tf * (self.K1 + 1)
                denominator = tf + self.K1 * (
                    1 - self.B + self.B * doc_len / max(self._avgdl, 1)
                )
                scores[chunk_id] += idf * (numerator / denominator)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[SearchHit] = []
        for rank, (cid, score) in enumerate(ranked[:top_k], start=1):
            chunk = self._chunks[cid]
            out.append(
                SearchHit(
                    id=cid,
                    text=chunk.text,
                    metadata=dict(chunk.metadata),
                    score=score,
                    score_kind="bm25",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _terms_for(
        self,
        chunk: Chunk,
        sparse_vectors: list[dict[str, Any]] | None,
        index: int,
    ) -> list[str]:
        if sparse_vectors is not None and index < len(sparse_vectors):
            sv = sparse_vectors[index]
            terms_dict = sv.get("terms", {}) if isinstance(sv, dict) else {}
            return [t for t, c in terms_dict.items() for _ in range(int(c))]
        return _WORD.findall(chunk.text.lower())

    def remove_document(self, source_path: str, collection: str) -> None:
        to_remove = [
            cid
            for cid, chunk in self._chunks.items()
            if chunk.metadata.get("source_path") == source_path
            and chunk.metadata.get("collection") == collection
        ]
        for cid in to_remove:
            self._chunks.pop(cid, None)
            self._doc_lens.pop(cid, None)
        # Rebuild inverted index from remaining docs.
        self._inverted = defaultdict(list)
        for chunk in self._chunks.values():
            terms = _WORD.findall(chunk.text.lower())
            for term in set(terms):
                self._inverted[term].append((chunk.id, terms.count(term)))
        self._doc_count = len(self._doc_lens)
        self._avgdl = (
            sum(self._doc_lens.values()) / self._doc_count if self._doc_count else 0.0
        )

    def save(self, path: str | Path) -> None:
        import json

        payload = {
            "chunks": [c.to_dict() for c in self._chunks.values()],
            "doc_lens": self._doc_lens,
            "avgdl": self._avgdl,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def load(self, path: str | Path) -> None:
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._chunks = {c["id"]: Chunk.from_dict(c) for c in data.get("chunks", [])}
        self._doc_lens = {k: int(v) for k, v in data.get("doc_lens", {}).items()}
        self._doc_count = len(self._doc_lens)
        self._avgdl = float(data.get("avgdl", 0.0))
        self._inverted = defaultdict(list)
        for chunk in self._chunks.values():
            terms = _WORD.findall(chunk.text.lower())
            for term in set(terms):
                self._inverted[term].append((chunk.id, terms.count(term)))


def _matches(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, expected in filters.items():
        if metadata.get(key) != expected:
            return False
    return True