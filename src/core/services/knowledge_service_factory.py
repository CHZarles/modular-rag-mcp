"""按配置装配 KnowledgeService 的本地实现。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.core.query_engine import (
    DenseRetriever,
    ExactMetadataFilter,
    HybridQueryEngine,
    HybridSearchConfig,
    NoneReranker,
    QueryProcessor,
    RRFFusion,
    SparseRetriever,
)
from src.core.response import ResponseBuilder as DefaultResponseBuilder
from src.core.services.knowledge_service import KnowledgeCatalog, KnowledgeService
from src.core.services.local_knowledge_service import LocalKnowledgeService, SQLiteKnowledgeCatalog
from src.core.settings import Settings
from src.ingestion.storage import BM25Indexer
from src.libs.embedding import create_embedding
from src.libs.loader import SQLiteIntegrityStore
from src.libs.reranker import create_reranker
from src.libs.vector_store import create_vector_store
from src.ports.query import QueryEngine
from src.ports.response import ResponseBuilder


@dataclass(frozen=True)
class KnowledgeServiceDependencies:
    """测试或自定义入口可替换的三个高层依赖。"""

    query_engine: QueryEngine | None = None
    response_builder: ResponseBuilder | None = None
    catalog: KnowledgeCatalog | None = None


def build_knowledge_service(
    settings: Settings,
    deps: KnowledgeServiceDependencies | None = None,
) -> KnowledgeService:
    """根据 ``knowledge_service.mode`` 创建统一知识服务。

    当前主线只实现零额外服务依赖的 local 模式。未来的 HTTP 实现仍需遵守同一个
    KnowledgeService 契约，但不会改变 MCP transport。
    """
    mode = _required_text(settings.knowledge_service, "mode", "knowledge_service").lower()
    if mode != "local":
        raise ValueError(f"Unsupported knowledge service mode: {mode}")

    selected = deps or KnowledgeServiceDependencies()
    integrity: SQLiteIntegrityStore | None = None
    if selected.query_engine is None or selected.catalog is None:
        storage = _required_mapping(settings.ingestion, "storage", "ingestion")
        integrity = SQLiteIntegrityStore(
            _required_text(storage, "integrity_db_path", "ingestion.storage")
        )

    query_engine = selected.query_engine
    if query_engine is None:
        if integrity is None:  # 仅用于让类型检查器确认上面的装配不变量。
            raise RuntimeError("local knowledge service requires an integrity store")
        query_engine = build_local_query_engine(settings, integrity_store=integrity)

    catalog = selected.catalog
    if catalog is None:
        if integrity is None:
            raise RuntimeError("local knowledge service requires an integrity store")
        catalog = SQLiteKnowledgeCatalog(integrity)

    return LocalKnowledgeService(
        query_engine=query_engine,
        response_builder=selected.response_builder or DefaultResponseBuilder(),
        catalog=catalog,
    )


def build_local_query_engine(
    settings: Settings,
    *,
    no_rerank: bool = False,
    integrity_store: SQLiteIntegrityStore | None = None,
) -> HybridQueryEngine:
    """装配 local 模式和查询 CLI 共用的混合检索引擎。"""
    storage = _required_mapping(settings.ingestion, "storage", "ingestion")
    integrity = integrity_store or SQLiteIntegrityStore(
        _required_text(storage, "integrity_db_path", "ingestion.storage")
    )
    retrieval = settings.retrieval
    enable_dense = _optional_bool(retrieval, "enable_dense", "retrieval", default=True)
    enable_sparse = _optional_bool(retrieval, "enable_sparse", "retrieval", default=True)
    if not enable_dense and not enable_sparse:
        raise ValueError("retrieval must enable at least one retrieval route")
    if enable_sparse and _required_text(retrieval, "sparse_backend", "retrieval").lower() != "bm25":
        raise ValueError("Unsupported sparse backend; local mode currently requires bm25")
    if _required_text(retrieval, "fusion_algorithm", "retrieval").lower() != "rrf":
        raise ValueError("Unsupported fusion algorithm; local mode currently requires rrf")

    rerank_backend = _required_text(settings.rerank, "backend", "rerank").lower()
    rerank_enabled = (
        not no_rerank
        and _optional_bool(settings.rerank, "enabled", "rerank", default=True)
        and rerank_backend != "none"
    )
    reranker = create_reranker(settings) if rerank_enabled else NoneReranker()
    final_top_k = _positive_int(retrieval, "top_k_final", "retrieval")
    fusion_top_k = final_top_k
    if rerank_enabled:
        top_m = _optional_positive_int(settings.rerank, "top_m", "rerank")
        if top_m is not None:
            fusion_top_k = max(fusion_top_k, top_m)

    return HybridQueryEngine(
        query_processor=QueryProcessor(),
        dense_retriever=(
            DenseRetriever(
                create_embedding(settings),
                create_vector_store(settings),
                generation_store=integrity,
            )
            if enable_dense
            else None
        ),
        sparse_retriever=(
            SparseRetriever(
                BM25Indexer(
                    _required_text(storage, "bm25_path", "ingestion.storage"),
                    generation_store=integrity,
                )
            )
            if enable_sparse
            else None
        ),
        fusion=RRFFusion(),
        metadata_filter=ExactMetadataFilter(),
        reranker=reranker,
        config=HybridSearchConfig(
            dense_top_k=_positive_int(retrieval, "top_k_dense", "retrieval"),
            sparse_top_k=_positive_int(retrieval, "top_k_sparse", "retrieval"),
            fusion_top_k=fusion_top_k,
            enable_dense=enable_dense,
            enable_sparse=enable_sparse,
        ),
    )


def _required_mapping(
    config: Mapping[str, Any],
    key: str,
    section: str,
) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _positive_int(config: Mapping[str, Any], key: str, section: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    return value


def _optional_positive_int(
    config: Mapping[str, Any],
    key: str,
    section: str,
) -> int | None:
    if config.get(key) is None:
        return None
    return _positive_int(config, key, section)


def _optional_bool(
    config: Mapping[str, Any],
    key: str,
    section: str,
    *,
    default: bool,
) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be a boolean")
    return value
