"""Chroma 向量存储适配器。

本模块负责在项目领域对象和 Chroma 原生数据结构之间转换：

- ``ChunkRecord.id`` -> Chroma ``ids``
- ``ChunkRecord.text`` -> Chroma ``documents``
- ``ChunkRecord.dense_vector`` -> Chroma ``embeddings``
- ``ChunkRecord.metadata`` -> Chroma ``metadatas``

Chroma 的 metadata 只适合保存字符串、数字、布尔值等标量，而领域层 metadata
还可能包含列表或嵌套字典。因此写入时会保存一份完整 JSON，同时展开其中的标量
字段用于过滤；读取时再从完整 JSON 还原，避免丢失图片引用、标签等结构化信息。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import chromadb

from src.core.types import ChunkRecord, CollectionInfo, JsonDict, SearchHit

# 这些字段只供适配器内部使用，不属于用户原始 metadata。
_METADATA_JSON_KEY = "_rag_metadata_json"
_SPARSE_VECTOR_JSON_KEY = "_rag_sparse_vector_json"
_CONTENT_HASH_KEY = "_rag_content_hash"
_RESERVED_KEYS = {_METADATA_JSON_KEY, _SPARSE_VECTOR_JSON_KEY, _CONTENT_HASH_KEY}


class ChromaStore:
    """把领域层的 ``ChunkRecord`` 映射到本地持久化 Chroma collection。

    一个实例只操作一个 collection。``trace`` 参数来自统一端口契约，当前适配器
    暂不写可观测数据，后续可以在不修改调用方的情况下接入。
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        # 三项配置都有可运行默认值；显式传入空字符串仍视为配置错误。
        persist_path = str(config.get("persist_path", "./data/db/chroma")).strip()
        collection_name = str(config.get("collection_name", "default")).strip()
        distance_metric = str(config.get("distance_metric", "cosine")).strip().lower()
        if not persist_path:
            raise ValueError("chroma configuration error: persist_path must not be empty")
        if not collection_name:
            raise ValueError("chroma configuration error: collection_name must not be empty")
        if distance_metric not in {"cosine", "l2", "ip"}:
            raise ValueError("chroma configuration error: unsupported distance_metric")

        # expanduser() 允许配置使用 ~/data/chroma；Chroma 负责创建实际目录。
        self.persist_path = Path(persist_path).expanduser()
        self.collection_name = collection_name

        # PersistentClient 将索引和正文写到磁盘，进程重启后可用相同路径重新打开。
        self._client = chromadb.PersistentClient(path=str(self.persist_path))

        # hnsw:space 决定距离计算方式。collection 已存在时 Chroma 会直接复用它。
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": distance_metric},
        )

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        """按 chunk id 幂等写入正文、向量和元数据。

        相同 id 再次写入会更新原记录，而不是新增重复数据。空批次直接返回；缺少
        dense vector 的记录无法参与向量检索，因此在调用 Chroma 前明确报错。
        """
        if not records:
            return

        # 先校验完整批次，避免参数准备到一半才发现某条记录没有向量。
        embeddings: list[list[float]] = []
        for record in records:
            if not record.dense_vector:
                raise ValueError(f"chroma upsert error: record {record.id!r} has no dense_vector")
            embeddings.append(record.dense_vector)

        # 四个列表按下标一一对应，并作为一个批次交给 Chroma。
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
        """按稠密向量检索，并返回统一的 ``SearchHit``。

        ``filters`` 会转换为 Chroma ``where`` 条件，只能过滤写入时展开的标量
        metadata。多个字段使用显式 ``$and``；返回的 ``score`` 是 distance，值越小
        表示越相似。
        """
        if not vector:
            raise ValueError("chroma query error: vector must not be empty")
        if top_k <= 0:
            raise ValueError("chroma query error: top_k must be positive")

        # 空 collection 不执行 query；同时用总记录数限制 n_results，兼容 Chroma
        # 对“请求数量大于现有记录数”的不同版本行为。
        count = self._collection.count()
        if count == 0:
            return []

        result = self._collection.query(
            query_embeddings=[vector],
            n_results=min(top_k, count),
            where=_to_where(filters) if filters else None,
            include=["documents", "metadatas", "distances"],
        )

        # Chroma 支持一次查询多个向量，因此返回二维数组。这里每次只传一个查询
        # 向量，只需要取各字段的第一行。
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
        """批量读取记录，并保持调用方给出的 id 顺序。

        Chroma 不承诺返回顺序与输入 ids 一致，所以先按 id 建立映射，再按调用方
        的顺序组装结果。不存在的 id 会被忽略。
        """
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

        # 先把 Chroma 原生结果恢复为领域对象，并以 id 索引。
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

        # 二次遍历输入 ids，保证上层合并稀疏检索结果时顺序稳定。
        return [records[record_id] for record_id in ids if record_id in records]

    def get_by_metadata(self, filters: JsonDict) -> list[ChunkRecord]:
        """按 metadata 等值条件读取 Chunk，供文档管理与浏览使用。"""
        if not filters:
            raise ValueError("chroma get error: filters must not be empty")
        result = self._collection.get(
            where=_to_where(filters),
            include=["documents", "metadatas"],
        )
        ids = list(result.get("ids") or [])
        documents = result.get("documents")
        metadatas = result.get("metadatas")
        records: list[ChunkRecord] = []
        for index, record_id in enumerate(ids):
            raw_metadata = _item_at(metadatas, index, {})
            records.append(
                ChunkRecord(
                    id=str(record_id),
                    text=str(_item_at(documents, index, "")),
                    metadata=_decode_metadata(raw_metadata),
                    sparse_vector=_decode_json_dict(raw_metadata, _SPARSE_VECTOR_JSON_KEY),
                    content_hash=_optional_string(raw_metadata, _CONTENT_HASH_KEY),
                )
            )
        return sorted(records, key=_chunk_record_order)

    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo:
        """汇总当前 Chroma 索引中的文档、Chunk 与图片引用数量。"""
        get_kwargs: dict[str, Any] = {"include": ["metadatas"]}
        if collection is not None:
            get_kwargs["where"] = {"collection": collection}
        result = self._collection.get(**get_kwargs)

        document_ids: set[str] = set()
        image_ids: set[str] = set()
        for raw_metadata in result.get("metadatas") or []:
            metadata = _decode_metadata(raw_metadata)
            document_id = metadata.get("doc_key") or metadata.get("source_path")
            if isinstance(document_id, str) and document_id:
                document_ids.add(document_id)
            image_refs = metadata.get("image_refs")
            if isinstance(image_refs, list):
                image_ids.update(str(image_id) for image_id in image_refs)

        return CollectionInfo(
            name=collection or self.collection_name,
            document_count=len(document_ids),
            chunk_count=len(result.get("ids") or []),
            image_count=len(image_ids),
        )

    def delete_by_metadata(self, filters: JsonDict) -> int:
        """删除匹配元数据条件的记录并返回删除数量。

        禁止空条件，避免误删整个 collection。Chroma 的 delete 不返回删除数量，
        因此先查询匹配 id，再按 id 删除。
        """
        if not filters:
            raise ValueError("chroma delete error: filters must not be empty")
        result = self._collection.get(where=_to_where(filters), include=[])
        ids = list(result.get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)


def _to_where(filters: JsonDict) -> JsonDict:
    """把通用的字段等值过滤转换为 Chroma 接受的显式 AND 条件。"""
    if len(filters) == 1:
        return dict(filters)
    return {"$and": [{key: value} for key, value in filters.items()]}


def _chunk_record_order(record: ChunkRecord) -> tuple[int, str]:
    chunk_index = record.metadata.get("chunk_index")
    order = (
        chunk_index
        if isinstance(chunk_index, int) and not isinstance(chunk_index, bool) and chunk_index >= 0
        else sys.maxsize
    )
    return order, record.id


def _encode_metadata(record: ChunkRecord) -> JsonDict:
    """把领域 metadata 编码成 Chroma 可接受的标量字典。

    完整 metadata 以 JSON 字符串保存，负责无损还原；顶层标量字段额外复制一份，
    负责支持 ``where={"collection": "docs"}`` 这类条件查询。
    """
    try:
        # sort_keys 让相同 metadata 得到稳定字符串，方便调试和比较持久化内容。
        encoded = json.dumps(record.metadata, ensure_ascii=False, sort_keys=True)
        sparse = (
            json.dumps(record.sparse_vector, ensure_ascii=False, sort_keys=True)
            if record.sparse_vector is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"chroma upsert error: record {record.id!r} metadata is not JSON") from exc

    metadata: JsonDict = {_METADATA_JSON_KEY: encoded}

    # 列表、字典和 None 不直接写入 Chroma；它们仍保存在上面的完整 JSON 中。
    for key, value in record.metadata.items():
        if key not in _RESERVED_KEYS and isinstance(value, str | int | float | bool):
            metadata[key] = value

    # 稀疏向量由 BM25 链路使用，不参与 Chroma 的稠密向量距离计算。
    if sparse is not None:
        metadata[_SPARSE_VECTOR_JSON_KEY] = sparse
    if record.content_hash is not None:
        metadata[_CONTENT_HASH_KEY] = record.content_hash
    return metadata


def _decode_metadata(raw: Any) -> JsonDict:
    """优先还原完整 metadata；旧数据或损坏 JSON 则回退到标量字段。"""
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

    # 回退结果必须排除适配器内部字段，避免它们泄漏到领域层。
    return {key: value for key, value in raw.items() if key not in _RESERVED_KEYS}


def _decode_json_dict(raw: Any, key: str) -> JsonDict | None:
    """从指定内部字段读取 JSON 字典，缺失或格式错误时返回 None。"""
    if not isinstance(raw, Mapping) or not isinstance(raw.get(key), str):
        return None
    try:
        value = json.loads(raw[key])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _optional_string(raw: Any, key: str) -> str | None:
    """安全读取可选字符串字段，避免把异常存储类型带入领域对象。"""
    if not isinstance(raw, Mapping):
        return None
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _first_row(value: Any) -> list[Any]:
    """从 Chroma query 的二维返回值中取第一个查询向量对应的结果。"""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        return []
    row = value[0]
    return list(row) if isinstance(row, Sequence) and not isinstance(row, str | bytes) else []


def _item_at(value: Any, index: int, default: Any) -> Any:
    """兼容 list 和 NumPy 数组，安全读取指定下标。"""
    if value is None:
        return default
    try:
        return value[index]
    except (IndexError, KeyError, TypeError):
        return default


def _vector_at(value: Any, index: int) -> list[float] | None:
    """把 Chroma/NumPy 返回的向量统一转换成领域层的 ``list[float]``。"""
    vector = _item_at(value, index, None)
    if vector is None:
        return None
    return [float(item) for item in vector]


__all__ = ["ChromaStore"]
