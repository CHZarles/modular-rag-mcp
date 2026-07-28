"""文档摄取流水线使用的端口契约。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.types import Chunk, ChunkRecord, Document, ImageRef, JsonDict, SearchHit


@runtime_checkable
class FileIntegrityStore(Protocol):
    """维护文件指纹与摄取状态，支持增量处理。"""

    def compute_sha256(self, source_path: str) -> str: ...
    def should_skip(self, file_hash: str, collection: str) -> bool: ...
    def mark_processing(self, file_hash: str, source_path: str, collection: str) -> None: ...
    def mark_success(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        chunk_count: int,
    ) -> None: ...
    def mark_failed(self, file_hash: str, source_path: str, collection: str, error: str) -> None: ...
    def remove_record(self, file_hash: str, collection: str) -> None: ...
    def list_processed(self, collection: str | None = None) -> list[JsonDict]: ...


@runtime_checkable
class BaseLoader(Protocol):
    """把受支持的源文件加载为统一 Document。"""

    supported_extensions: tuple[str, ...]

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document: ...


@runtime_checkable
class BaseSplitter(Protocol):
    """把长文本切分为有序文本片段。"""

    def split_text(self, text: str, trace: Any | None = None) -> list[str]: ...


@runtime_checkable
class DocumentChunker(Protocol):
    """为切分结果补充 Chunk 领域信息。"""

    def split_document(self, document: Document, trace: Any | None = None) -> list[Chunk]: ...


@runtime_checkable
class BaseTransform(Protocol):
    """对一批 Chunk 执行可插拔增强或清洗。"""

    name: str

    def transform(self, chunks: list[Chunk], trace: Any | None = None) -> list[Chunk]: ...


@runtime_checkable
class BaseEmbedding(Protocol):
    """批量生成稠密文本向量。"""

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]: ...


@runtime_checkable
class SparseEncoder(Protocol):
    """批量生成用于关键词检索的稀疏表示。"""

    def encode(self, chunks: list[Chunk], trace: Any | None = None) -> list[JsonDict]: ...


@runtime_checkable
class BaseVectorStore(Protocol):
    """定义向量记录写入、查询和删除能力。"""

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None: ...
    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]: ...
    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]: ...
    def delete_by_metadata(self, filters: JsonDict) -> int: ...


@runtime_checkable
class BM25IndexStore(Protocol):
    """定义 BM25 索引写入、查询和按文档删除能力。"""

    def upsert(
        self,
        chunks: list[Chunk],
        sparse_vectors: list[JsonDict],
        trace: Any | None = None,
    ) -> None: ...
    def query(
        self,
        keywords: list[str],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]: ...
    def remove_document(self, source_path: str, collection: str) -> None: ...


@runtime_checkable
class ImageStore(Protocol):
    """持久化并按文档管理图片引用。"""

    def save_refs(self, images: list[ImageRef], trace: Any | None = None) -> None: ...
    def get(self, image_id: str) -> ImageRef | None: ...
    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]: ...
    def delete_by_document(self, source_path: str, collection: str) -> int: ...
