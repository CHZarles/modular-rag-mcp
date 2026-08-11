"""知识服务契约、本地实现与配置工厂。"""

from src.core.services.grep_service import (
    GrepMatch,
    GrepResponse,
    GrepService,
    active_generation_counts,
)
from src.core.services.knowledge_service import KnowledgeCatalog, KnowledgeService
from src.core.services.knowledge_service_factory import (
    KnowledgeServiceDependencies,
    build_knowledge_service,
    build_local_query_engine,
)
from src.core.services.local_knowledge_service import (
    LocalKnowledgeService,
    SQLiteKnowledgeCatalog,
)

__all__ = [
    "KnowledgeCatalog",
    "KnowledgeService",
    "KnowledgeServiceDependencies",
    "LocalKnowledgeService",
    "GrepMatch",
    "GrepResponse",
    "GrepService",
    "active_generation_counts",
    "SQLiteKnowledgeCatalog",
    "build_local_query_engine",
    "build_knowledge_service",
]
