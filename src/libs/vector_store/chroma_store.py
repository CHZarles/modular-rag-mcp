"""Chroma 向量存储适配器。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import chromadb

from src.core.types import ChunkRecord, JsonDict, SearchHit

_METADATA_JSON_KEY = "_rag_metadata_json"
_SPARSE_VECTOR_JSON_KEY = "_rag_sparse_vector_json"
_CONTENT_HASH_KEY = "_rag_content_hash"
_RESERVED_KEYS = {_METADATA_JSON_KEY, _SPARSE_VECTOR_JSON_KEY, _CONTENT_HASH_KEY}


class ChromaStore:
    """把领域层的 ChunkRecord 映射到本地持久化 Chroma collection。"""

    def __init__(self, config: Mapping[str, Any]) -> None:
        persist_path = str(config.get("persist_path", "./data/db/chroma")).strip()
        collection_name = str(config.get("collection_name", "default")).strip()
        distance_metric = str(config.get("distance_metric", "cosine")).strip().lower()
        if not persist_path:
            raise ValueError("chroma configuration error: persist_path must not be empty")
        if not collection_name:
            raise ValueError("chroma configuration error: collection_name must not be empty")
        if distance_metric not in {"cosine", "l2", "ip"}:
            raise ValueError("chroma configuration error: unsupported distance_metric")

        self.persist_path = Path(persist_path).expanduser()
        self.collection_name = collection_name
        self._client = chromadb.PersistentClient(path=str(self.persist_path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": distance_metric},
        )

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        """按 chunk id 幂等写入正文、向量和元数据。"""
        if not records:
            return

        embeddings: list[list[float]] = []
        for record in records:
            if not record.dense_vector:
                raise ValueError(f"chroma upsert error: record {record.id!r} has no dense_vector")
            embeddings.append(record.dense_vector)

        self._collection.upsert(
            ids=[record.id for record in records],
            documents=[record.text for record in records],
            embeddings=embeddings,
            metadatas=[_encode_metadata(record) for record in records],
        )

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        """按稠密向量检索，并以 Chroma distance 表示命中分数。"""
        if not vector:
            raise ValueError("chroma query error: vector must not be empty")
        if top_k <= 0:
            raise ValueError("chroma query error: top_k must be positive")
        count = self._collection.count()
        if count == 0:
            return []

        result = self._collection.query(
            query_embeddings=[vector],
            n_results=min(top_k, count),
            where=dict(filters) if filters else None,
            include=["documents", "metadatas", "distances"],
        )
        ids = _first_row(result.get("ids"))
        documents = _first_row(result.get("documents"))
        metadatas = _first_row(result.get("metadatas"))
        distances = _first_row(result.get("distances"))
        return [
            SearchHit(
                id=str(record_id),
                text=str(documents[index]),
                metadata=_decode_metadata(metadatas[index]),
                score=float(distances[index]),
                score_kind="distance",
            )
            for index, record_id in enumerate(ids)
        ]

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        """批量读取记录，并保持调用方给出的 id 顺序。"""
        if not ids:
            return []
        result = self._collection.get(
            ids=ids,
            include=["documents", "metadatas", "embeddings"],
        )
        result_ids = list(result.get("ids") or [])
        documents = result.get("documents")
        metadatas = result.get("metadatas")
        embeddings = result.get("embeddings")
        records: dict[str, ChunkRecord] = {}
        for index, record_id in enumerate(result_ids):
            raw_metadata = _item_at(metadatas, index, {})
            records[str(record_id)] = ChunkRecord(
                id=str(record_id),
                text=str(_item_at(documents, index, "")),
                metadata=_decode_metadata(raw_metadata),
                dense_vector=_vector_at(embeddings, index),
                sparse_vector=_decode_json_dict(raw_metadata, _SPARSE_VECTOR_JSON_KEY),
                content_hash=_optional_string(raw_metadata, _CONTENT_HASH_KEY),
            )
        return [records[record_id] for record_id in ids if record_id in records]

    def delete_by_metadata(self, filters: JsonDict) -> int:
        """删除匹配元数据条件的记录并返回删除数量。"""
        if not filters:
            raise ValueError("chroma delete error: filters must not be empty")
        result = self._collection.get(where=dict(filters), include=[])
        ids = list(result.get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)


def _encode_metadata(record: ChunkRecord) -> JsonDict:
    """保留完整 JSON 元数据，同时展开标量字段供 Chroma 过滤。"""
    try:
        encoded = json.dumps(record.metadata, ensure_ascii=False, sort_keys=True)
        sparse = (
            json.dumps(record.sparse_vector, ensure_ascii=False, sort_keys=True)
            if record.sparse_vector is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"chroma upsert error: record {record.id!r} metadata is not JSON") from exc

    metadata: JsonDict = {_METADATA_JSON_KEY: encoded}
    for key, value in record.metadata.items():
        if key not in _RESERVED_KEYS and isinstance(value, str | int | float | bool):
            metadata[key] = value
    if sparse is not None:
        metadata[_SPARSE_VECTOR_JSON_KEY] = sparse
    if record.content_hash is not None:
        metadata[_CONTENT_HASH_KEY] = record.content_hash
    return metadata


def _decode_metadata(raw: Any) -> JsonDict:
    if not isinstance(raw, Mapping):
        return {}
    encoded = raw.get(_METADATA_JSON_KEY)
    if isinstance(encoded, str):
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
    return {key: value for key, value in raw.items() if key not in _RESERVED_KEYS}


def _decode_json_dict(raw: Any, key: str) -> JsonDict | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get(key), str):
        return None
    try:
        value = json.loads(raw[key])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _optional_string(raw: Any, key: str) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _first_row(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        return []
    row = value[0]
    return list(row) if isinstance(row, Sequence) and not isinstance(row, str | bytes) else []


def _item_at(value: Any, index: int, default: Any) -> Any:
    if value is None:
        return default
    try:
        return value[index]
    except (IndexError, KeyError, TypeError):
        return default


def _vector_at(value: Any, index: int) -> list[float] | None:
    vector = _item_at(value, index, None)
    if vector is None:
        return None
    return [float(item) for item in vector]


__all__ = ["ChromaStore"]
