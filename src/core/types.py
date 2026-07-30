"""模块化 RAG 系统使用的稳定领域契约。

这些类型刻意保持精简且与供应商无关。具体适配器可把额外信息存入 ``metadata``
或 ``debug``，避免后端对象跨越模块边界泄漏。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

JsonDict = dict[str, Any]
Metadata = dict[str, Any]
Vector = list[float]
SparseVector = JsonDict
ProgressCallback = Callable[[str, int, int], None]
ClaimStatus = Literal["acquired", "already_succeeded", "in_progress"]


class SerializableDataclass:
    """为可序列化领域对象提供统一字典转换的 Mixin。"""

    def to_dict(self) -> JsonDict:
        # 所有实际子类都由 @dataclass 装饰，但 mypy 无法从 Mixin 类型推断这一点。
        return asdict(self)  # type: ignore[call-overload]


@dataclass(frozen=True)
class ClaimHandle(SerializableDataclass):
    """一次分代摄取领取的完整凭证。

    ``generation`` 隔离不同尝试写入的物理数据，``claim_token`` 则授权本次尝试续租、失败或
    发布。二者必须同时匹配，不能再用可重复的 worker 名称单独代表所有权。
    """

    doc_key: str
    generation: int
    source_revision: str
    claim_token: str
    lease_owner: str
    lease_expires_at: float


@dataclass(frozen=True)
class ClaimResult(SerializableDataclass):
    """任务领取结果；只有 ``acquired`` 会携带后续操作所需的凭证。"""

    status: ClaimStatus
    handle: ClaimHandle | None = None

    def __post_init__(self) -> None:
        if (self.status == "acquired") != (self.handle is not None):
            raise ValueError("claim result must include a handle exactly when acquired")


@dataclass(frozen=True)
class ImageRef(SerializableDataclass):
    """文档内图片的存储位置、来源及文本定位信息。"""

    image_id: str
    path: str
    collection: str
    source_path: str
    page: int | None = None
    mime_type: str = "image/png"
    text_offset: int | None = None
    text_length: int | None = None
    position: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> ImageRef:
        payload = dict(data)
        if "image_id" not in payload and "id" in payload:
            payload["image_id"] = payload.pop("id")
        return cls(**payload)


@dataclass(frozen=True)
class Document(SerializableDataclass):
    """Loader 产出的规范化文档。

    metadata 约定至少包含 ``source_path``；图片使用 ``images`` 列表记录，并在
    text 的原始位置使用 ``[IMAGE: image_id]`` 占位符。
    """

    id: str
    text: str
    metadata: Metadata

    @classmethod
    def from_dict(cls, data: JsonDict) -> Document:
        return cls(**data)


@dataclass(frozen=True)
class Chunk(SerializableDataclass):
    """带来源和原文偏移量的可检索文本块，metadata 继承文档的 source_path。"""

    id: str
    text: str
    metadata: Metadata
    source_ref: str
    chunk_index: int
    start_offset: int | None = None
    end_offset: int | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> Chunk:
        return cls(**data)


@dataclass(frozen=True)
class ChunkRecord(SerializableDataclass):
    """准备写入索引的文本块及其稠密、稀疏向量，metadata 保留来源信息。"""

    id: str
    text: str
    metadata: Metadata
    dense_vector: list[float] | None = None
    sparse_vector: JsonDict | None = None
    content_hash: str | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> ChunkRecord:
        return cls(**data)

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        dense_vector: list[float] | None = None,
        sparse_vector: JsonDict | None = None,
        content_hash: str | None = None,
    ) -> ChunkRecord:
        return cls(
            id=chunk.id,
            text=chunk.text,
            metadata=dict(chunk.metadata),
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            content_hash=content_hash,
        )


@dataclass(frozen=True)
class ProcessedQuery(SerializableDataclass):
    """完成规范化、关键词提取和过滤解析后的查询。"""

    original_query: str
    standalone_query: str
    keywords: list[str]
    filters: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> ProcessedQuery:
        return cls(**data)


@dataclass(frozen=True)
class SearchHit(SerializableDataclass):
    """由底层检索后端返回的统一命中结果。"""

    id: str
    text: str
    metadata: Metadata
    score: float
    score_kind: Literal["similarity", "distance", "bm25", "unknown"] = "unknown"
    raw: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> SearchHit:
        return cls(**data)


@dataclass(frozen=True)
class RetrievalResult(SerializableDataclass):
    """一次检索命中的稳定公共字段。"""

    chunk_id: str
    text: str
    metadata: Metadata
    score: float

    @classmethod
    def from_dict(cls, data: JsonDict) -> RetrievalResult:
        return cls(**data)


@dataclass(frozen=True)
class RetrievalCandidate(RetrievalResult):
    """在召回、融合和重排阶段之间传递的扩展候选项。"""

    source: Literal["dense", "sparse", "fusion", "rerank"]
    rank: int
    debug: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> RetrievalCandidate:
        return cls(**data)


@dataclass(frozen=True)
class Citation(SerializableDataclass):
    """面向最终响应的可追溯引用。"""

    citation_id: str
    chunk_id: str
    source_path: str
    page: int | None
    text: str
    score: float
    metadata: Metadata = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> Citation:
        return cls(**data)


@dataclass(frozen=True)
class ImagePayload(SerializableDataclass):
    """响应中的图片内容或资源地址。"""

    image_id: str
    mime_type: str
    data_base64: str | None = None
    uri: str | None = None
    source_ref: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> ImagePayload:
        return cls(**data)


@dataclass(frozen=True)
class QueryRequest(SerializableDataclass):
    """知识查询请求及其检索范围。"""

    query: str
    top_k: int = 5
    collection: str = "default"
    filters: JsonDict = field(default_factory=dict)
    include_images: bool = True
    request_id: str | None = None

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")

    @classmethod
    def from_dict(cls, data: JsonDict) -> QueryRequest:
        return cls(**data)


@dataclass(frozen=True)
class QueryResponse(SerializableDataclass):
    """包含答案、引用、候选项和图片的统一查询响应。"""

    answer: str
    citations: list[Citation]
    items: list[RetrievalCandidate]
    images: list[ImagePayload] = field(default_factory=list)
    request_id: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> QueryResponse:
        # 嵌套对象可能来自 JSON，因此在构造响应前恢复为强类型领域对象。
        payload = dict(data)
        payload["citations"] = [
            item if isinstance(item, Citation) else Citation.from_dict(item)
            for item in payload.get("citations", [])
        ]
        payload["items"] = [
            item if isinstance(item, RetrievalCandidate) else RetrievalCandidate.from_dict(item)
            for item in payload.get("items", [])
        ]
        payload["images"] = [
            item if isinstance(item, ImagePayload) else ImagePayload.from_dict(item)
            for item in payload.get("images", [])
        ]
        return cls(**payload)


@dataclass(frozen=True)
class IngestionRequest(SerializableDataclass):
    """文档摄取请求。"""

    source_path: str
    collection: str = "default"
    force: bool = False
    request_id: str | None = None

    @classmethod
    def from_dict(cls, data: JsonDict) -> IngestionRequest:
        return cls(**data)


@dataclass(frozen=True)
class IngestionResult(SerializableDataclass):
    """文档摄取的成功、跳过或失败结果。"""

    source_path: str
    collection: str
    status: Literal["success", "skipped", "failed"]
    file_hash: str
    document_id: str | None = None
    chunk_count: int = 0
    image_count: int = 0
    error: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> IngestionResult:
        return cls(**data)


@dataclass(frozen=True)
class CollectionInfo(SerializableDataclass):
    """知识集合的文档、Chunk 与图片统计。"""

    name: str
    document_count: int
    chunk_count: int
    image_count: int = 0
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> CollectionInfo:
        return cls(**data)


@dataclass(frozen=True)
class DocumentSummary(SerializableDataclass):
    """用于列表和摘要 Tool 的轻量文档信息。"""

    doc_id: str
    source_path: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> DocumentSummary:
        return cls(**data)


@dataclass(frozen=True)
class DeleteResult(SerializableDataclass):
    """跨向量库、BM25、图片与完整性记录的删除结果。"""

    source_path: str
    collection: str
    deleted_chunks: int
    deleted_images: int
    removed_bm25: bool
    removed_integrity_record: bool
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: JsonDict) -> DeleteResult:
        return cls(**data)


@dataclass(frozen=True)
class EvaluationCase(SerializableDataclass):
    """单条评估问题及其期望命中或答案。"""

    case_id: str
    query: str
    expected_chunk_ids: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> EvaluationCase:
        return cls(**data)


@dataclass(frozen=True)
class EvaluationReport(SerializableDataclass):
    """一次评估运行的汇总指标和逐用例明细。"""

    run_id: str
    metrics: dict[str, float]
    cases: list[JsonDict]
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> EvaluationReport:
        return cls(**data)
