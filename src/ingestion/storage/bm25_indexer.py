"""本地 BM25 倒排索引的构建、查询与快照持久化。"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import pickle
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any, TypedDict

from src.core.types import Chunk, ChunkRecord, JsonDict, SearchHit
from src.ingestion.embedding import tokenize
from src.ports.ingestion import GenerationStateStore

_SNAPSHOT_VERSION = 2
_PATH_LOCKS: dict[str, RLock] = {}
_PATH_LOCKS_GUARD = Lock()


class _StoredDocument(TypedDict):
    text: str
    metadata: JsonDict
    terms: dict[str, int]
    doc_length: int


class _Posting(TypedDict):
    chunk_id: str
    tf: int
    doc_length: int


class _TermEntry(TypedDict):
    idf: float
    postings: list[_Posting]


class BM25Indexer:
    """以 Chunk ID 幂等维护可持久化的本地 BM25 索引。

    ``generation_store=None`` 是旧索引兼容模式；正式分代查询必须注入控制面，才能只用
    active generation 重算统计并过滤命中。
    """

    def __init__(
        self,
        persist_path: str | Path = "data/db/bm25",
        *,
        k1: float = 1.5,
        b: float = 0.75,
        generation_store: GenerationStateStore | None = None,
    ) -> None:
        if k1 <= 0:
            raise ValueError("bm25 configuration error: k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("bm25 configuration error: b must be between 0 and 1")

        self.persist_path = Path(persist_path).expanduser()
        self.index_path = self.persist_path / "index.pkl"
        self.lock_path = self.persist_path / "index.lock"
        self._thread_lock = _path_lock(self.lock_path)
        self._loaded_mtime_ns: int | None = None
        self.k1 = float(k1)
        self.b = float(b)
        self.generation_store = generation_store
        self._documents: dict[str, _StoredDocument] = {}
        self.inverted_index: dict[str, _TermEntry] = {}
        self.document_count = 0
        self.average_document_length = 0.0
        if self.index_path.exists():
            self.load()

    def build(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[JsonDict],
        trace: Any | None = None,
    ) -> None:
        """使用给定语料重建索引，替换已有全部内容。"""
        documents = _prepare_documents(chunks, sparse_vectors)
        with self._file_lock(exclusive=True):
            self._replace_state(documents)

    def upsert(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[JsonDict],
        trace: Any | None = None,
    ) -> None:
        """按 Chunk ID 更新或插入文档，并重算全局 IDF。"""
        updates = _prepare_documents(chunks, sparse_vectors)
        if not updates:
            return
        # 写锁内先重载最新快照再修改，避免两个 BM25Indexer 实例各自基于旧内存状态写回。
        with self._file_lock(exclusive=True):
            self._load_unlocked()
            documents = dict(self._documents)
            documents.update(updates)
            self._replace_state(documents)

    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        """对规范化后的关键词计算 BM25 分数并稳定返回 Top-K。"""
        if top_k <= 0:
            raise ValueError("bm25 query error: top_k must be positive")
        if any(not isinstance(keyword, str) for keyword in keywords):
            raise ValueError("bm25 query error: keywords must be strings")

        # 查询与 C9 共用同一个分词函数，避免写入和查询的规范化规则漂移。
        query_terms = list(
            dict.fromkeys(term for keyword in keywords for term in tokenize(keyword))
        )
        documents = self._queryable_documents(filters, self._snapshot_documents())
        if not query_terms or not documents:
            return []

        # 非 active generation 不能参与 N、df、IDF 或平均长度，否则即使最后过滤命中，
        # BM25 分数仍会被旧版本污染。查询时从可见文档重新计算统计以保证正确性。
        inverted_index, average_document_length = _build_inverted_index(documents)

        scores: dict[str, float] = {}
        matched_terms: dict[str, list[str]] = {}
        for term in query_terms:
            term_entry = inverted_index.get(term)
            if term_entry is None:
                continue
            for posting in term_entry["postings"]:
                chunk_id = posting["chunk_id"]
                tf = posting["tf"]
                length_ratio = posting["doc_length"] / average_document_length
                denominator = tf + self.k1 * (1 - self.b + self.b * length_ratio)
                score = term_entry["idf"] * (tf * (self.k1 + 1)) / denominator
                scores[chunk_id] = scores.get(chunk_id, 0.0) + score
                matched_terms.setdefault(chunk_id, []).append(term)

        # 分数相同则按稳定 Chunk ID 排序，使持久化重载后的结果完全可复现。
        ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:top_k]
        return [
            SearchHit(
                id=chunk_id,
                text=documents[chunk_id]["text"],
                metadata=dict(documents[chunk_id]["metadata"]),
                score=scores[chunk_id],
                score_kind="bm25",
                raw={
                    "matched_terms": matched_terms[chunk_id],
                    "doc_length": documents[chunk_id]["doc_length"],
                },
            )
            for chunk_id in ranked_ids
        ]

    def remove_document(self, source_path: str, collection: str) -> None:
        """删除指定 collection 中属于同一源文件的全部 Chunk。"""
        with self._file_lock(exclusive=True):
            self._load_unlocked()
            documents = {
                chunk_id: document
                for chunk_id, document in self._documents.items()
                if not (
                    document["metadata"].get("source_path") == source_path
                    and document["metadata"].get("collection") == collection
                )
            }
            if len(documents) != len(self._documents):
                self._replace_state(documents)

    def remove_generation(self, doc_key: str, generation: int) -> int:
        """精确删除一个不可见 generation，绝不按路径清空其他版本。"""
        with self._file_lock(exclusive=True):
            self._load_unlocked()
            documents = {
                chunk_id: document
                for chunk_id, document in self._documents.items()
                if not (
                    document["metadata"].get("doc_key") == doc_key
                    and document["metadata"].get("generation") == generation
                )
            }
            removed = len(self._documents) - len(documents)
            if removed:
                self._replace_state(documents)
            return removed

    def list_active_chunk_records(self, collection: str) -> list[ChunkRecord]:
        """Export final active Chunk text for the offline grep-index rebuild."""
        if self.generation_store is None:
            raise ValueError("bm25 export error: generation store is required")
        active = self.generation_store.get_active_generations(collection)
        documents = self._snapshot_documents()
        records: list[ChunkRecord] = []
        for chunk_id, document in documents.items():
            metadata = dict(document["metadata"])
            if metadata.get("collection") != collection or not _metadata_is_active(metadata, active):
                continue
            text = document["text"]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            doc_key = metadata.get("doc_key")
            generation = metadata.get("generation")
            chunk_index = metadata.get("chunk_index")
            if (
                not isinstance(doc_key, str)
                or not isinstance(generation, int)
                or isinstance(generation, bool)
                or not isinstance(chunk_index, int)
                or isinstance(chunk_index, bool)
                or chunk_index < 0
            ):
                raise ValueError("bm25 export error: active chunk identity is invalid")
            suffix = hashlib.sha256(f"{chunk_index}\0{content_hash}".encode()).hexdigest()[:32]
            if chunk_id != f"{doc_key}:{generation}:{suffix}":
                raise ValueError("bm25 export error: active chunk id does not match text")
            records.append(
                ChunkRecord(
                    id=chunk_id,
                    text=text,
                    metadata=metadata,
                    content_hash=content_hash,
                )
            )
        return sorted(
            records,
            key=lambda record: (
                str(record.metadata.get("source_path", "")),
                _sort_integer(record.metadata.get("page"), fallback=-1),
                _sort_integer(record.metadata.get("chunk_index"), fallback=-1),
                record.id,
            ),
        )

    def _queryable_documents(
        self,
        filters: JsonDict | None,
        source_documents: dict[str, _StoredDocument],
    ) -> dict[str, _StoredDocument]:
        documents = {
            chunk_id: document
            for chunk_id, document in source_documents.items()
            if not filters or _matches_filters(document["metadata"], filters)
        }
        if self.generation_store is None:
            return documents

        collection = filters.get("collection") if filters else None
        active = self.generation_store.get_active_generations(
            collection if isinstance(collection, str) else None
        )
        return {
            chunk_id: document
            for chunk_id, document in documents.items()
            if _metadata_is_active(document["metadata"], active)
        }

    def load(self) -> None:
        """从磁盘重新加载快照；缺少快照时恢复为空索引。"""
        with self._file_lock(exclusive=False):
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        """调用方持有文件锁时重载快照。"""
        if not self.index_path.exists():
            self._set_state({}, {}, 0.0)
            self._loaded_mtime_ns = None
            return
        try:
            with self.index_path.open("rb") as handle:
                snapshot = pickle.load(handle)  # noqa: S301 - 仅加载本地受控索引快照。
            documents, stored_index, stored_average = _validate_snapshot(snapshot)
            rebuilt_index, rebuilt_average = _build_inverted_index(documents)
            if stored_index != rebuilt_index or stored_average != rebuilt_average:
                raise ValueError("snapshot statistics do not match stored documents")
        except Exception as exc:
            raise ValueError(
                f"bm25 index load error: {self.index_path}; remove the index and re-ingest all documents"
            ) from exc
        self._set_state(documents, stored_index, stored_average)
        self._loaded_mtime_ns = self.index_path.stat().st_mtime_ns

    def _snapshot_documents(self) -> dict[str, _StoredDocument]:
        """在共享锁内按需刷新，并返回本次查询使用的稳定文档快照。"""
        with self._file_lock(exclusive=False):
            current_mtime = self.index_path.stat().st_mtime_ns if self.index_path.exists() else None
            if current_mtime != self._loaded_mtime_ns:
                self._load_unlocked()
            return dict(self._documents)

    @contextmanager
    def _file_lock(self, *, exclusive: bool):
        """组合进程内路径锁和 ``flock``，保护整次重载、修改与原子换档。"""
        self.persist_path.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _replace_state(self, documents: dict[str, _StoredDocument]) -> None:
        """先生成并持久化完整候选状态，成功后再替换内存状态。"""
        inverted_index, average_length = _build_inverted_index(documents)
        self._persist(documents, inverted_index, average_length)
        self._set_state(documents, inverted_index, average_length)

    def _set_state(
        self,
        documents: dict[str, _StoredDocument],
        inverted_index: dict[str, _TermEntry],
        average_length: float,
    ) -> None:
        self._documents = documents
        self.inverted_index = inverted_index
        self.document_count = len(documents)
        self.average_document_length = average_length

    def _persist(
        self,
        documents: dict[str, _StoredDocument],
        inverted_index: dict[str, _TermEntry],
        average_length: float,
    ) -> None:
        """写入临时快照后原子替换正式文件，失败时保留旧索引。"""
        self.persist_path.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_suffix(".pkl.tmp")
        snapshot = {
            "version": _SNAPSHOT_VERSION,
            "documents": documents,
            "inverted_index": inverted_index,
            "document_count": len(documents),
            "average_document_length": average_length,
        }
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.persist_path,
                prefix="index.pkl.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                pickle.dump(snapshot, handle, protocol=pickle.HIGHEST_PROTOCOL)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.index_path)
            self._loaded_mtime_ns = self.index_path.stat().st_mtime_ns
        finally:
            temporary_path.unlink(missing_ok=True)


def _prepare_documents(
    chunks: list[Chunk],
    sparse_vectors: list[JsonDict],
) -> dict[str, _StoredDocument]:
    """在修改索引前完整校验 Chunk 与稀疏统计的对齐关系。"""
    if len(chunks) != len(sparse_vectors):
        raise ValueError("bm25 index error: sparse vector count must match chunk count")

    documents: dict[str, _StoredDocument] = {}
    for chunk, sparse_vector in zip(chunks, sparse_vectors, strict=True):
        if chunk.id in documents:
            raise ValueError(f"bm25 index error: duplicate chunk id {chunk.id!r}")
        terms, doc_length = _validate_statistics(sparse_vector, chunk.id)
        documents[chunk.id] = {
            "text": chunk.text,
            "metadata": dict(chunk.metadata),
            "terms": terms,
            "doc_length": doc_length,
        }
    return documents


def _validate_statistics(sparse_vector: object, chunk_id: str) -> tuple[dict[str, int], int]:
    if not isinstance(sparse_vector, dict):
        raise ValueError(f"bm25 index error: chunk {chunk_id!r} statistics must be a mapping")
    terms = sparse_vector.get("terms")
    doc_length = sparse_vector.get("doc_length")
    if not isinstance(terms, dict):
        raise ValueError(f"bm25 index error: chunk {chunk_id!r} terms must be a mapping")
    if not isinstance(doc_length, int) or isinstance(doc_length, bool) or doc_length < 0:
        raise ValueError(f"bm25 index error: chunk {chunk_id!r} doc_length must be non-negative")

    validated_terms: dict[str, int] = {}
    for term, frequency in terms.items():
        if not isinstance(term, str) or not term:
            raise ValueError(f"bm25 index error: chunk {chunk_id!r} term must be non-empty")
        if not isinstance(frequency, int) or isinstance(frequency, bool) or frequency <= 0:
            raise ValueError(
                f"bm25 index error: chunk {chunk_id!r} term frequency must be a positive integer"
            )
        validated_terms[term] = frequency
    if sum(validated_terms.values()) != doc_length:
        raise ValueError(
            f"bm25 index error: chunk {chunk_id!r} doc_length must equal total term frequency"
        )
    return validated_terms, doc_length


def _build_inverted_index(
    documents: dict[str, _StoredDocument],
) -> tuple[dict[str, _TermEntry], float]:
    """从文档表重算倒排 postings、全局 IDF 与平均文档长度。"""
    document_count = len(documents)
    average_length = (
        sum(document["doc_length"] for document in documents.values()) / document_count
        if document_count
        else 0.0
    )
    postings_by_term: dict[str, list[_Posting]] = {}
    for chunk_id in sorted(documents):
        document = documents[chunk_id]
        for term, frequency in sorted(document["terms"].items()):
            postings_by_term.setdefault(term, []).append(
                {
                    "chunk_id": chunk_id,
                    "tf": frequency,
                    "doc_length": document["doc_length"],
                }
            )

    inverted_index: dict[str, _TermEntry] = {}
    for term in sorted(postings_by_term):
        postings = postings_by_term[term]
        document_frequency = len(postings)
        idf = math.log(
            (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        inverted_index[term] = {"idf": idf, "postings": postings}
    return inverted_index, average_length


def _matches_filters(metadata: JsonDict, filters: JsonDict) -> bool:
    return all(metadata.get(key) == value for key, value in filters.items())


def _metadata_is_active(metadata: JsonDict, active: dict[str, int]) -> bool:
    doc_key = metadata.get("doc_key")
    generation = metadata.get("generation")
    return (
        isinstance(doc_key, str)
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and active.get(doc_key) == generation
    )


def _sort_integer(value: object, *, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _path_lock(path: Path) -> RLock:
    """让同一进程内指向同一索引文件的实例复用一把可重入锁。"""
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, RLock())


def _validate_snapshot(
    snapshot: object,
) -> tuple[dict[str, _StoredDocument], dict[str, _TermEntry], float]:
    """校验快照顶层结构，并用文档表恢复强类型数据。"""
    if not isinstance(snapshot, dict) or snapshot.get("version") != _SNAPSHOT_VERSION:
        raise ValueError("unsupported snapshot version")
    raw_documents = snapshot.get("documents")
    raw_index = snapshot.get("inverted_index")
    raw_count = snapshot.get("document_count")
    raw_average = snapshot.get("average_document_length")
    if not isinstance(raw_documents, dict) or not isinstance(raw_index, dict):
        raise ValueError("snapshot documents and index must be mappings")
    if not isinstance(raw_count, int) or raw_count != len(raw_documents):
        raise ValueError("snapshot document count mismatch")
    if not isinstance(raw_average, int | float) or isinstance(raw_average, bool):
        raise ValueError("snapshot average document length must be numeric")

    documents: dict[str, _StoredDocument] = {}
    for chunk_id, raw_document in raw_documents.items():
        if not isinstance(chunk_id, str) or not isinstance(raw_document, dict):
            raise ValueError("snapshot document entry is invalid")
        text = raw_document.get("text")
        metadata = raw_document.get("metadata")
        if not isinstance(text, str) or not isinstance(metadata, dict):
            raise ValueError("snapshot document content is invalid")
        terms, doc_length = _validate_statistics(raw_document, chunk_id)
        documents[chunk_id] = {
            "text": text,
            "metadata": dict(metadata),
            "terms": terms,
            "doc_length": doc_length,
        }

    # 重建后的精确比较会继续验证倒排索引的嵌套结构和统计值。
    stored_index: dict[str, _TermEntry] = raw_index  # type: ignore[assignment]
    return documents, stored_index, float(raw_average)


__all__ = ["BM25Indexer"]
