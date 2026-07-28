"""应用服务契约及其轻量本地封装。"""

from src.application.services import (
    ComponentRegistry,
    DocumentService,
    EvaluationService,
    IngestionService,
    KnowledgeService,
    LocalIngestionService,
    LocalKnowledgeService,
)

__all__ = [
    "ComponentRegistry",
    "DocumentService",
    "EvaluationService",
    "IngestionService",
    "KnowledgeService",
    "LocalIngestionService",
    "LocalKnowledgeService",
]
