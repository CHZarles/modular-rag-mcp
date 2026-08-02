"""文档摄取流程编排。"""

from src.ingestion.document_manager import DocumentManager
from src.ingestion.factory import build_ingestion_pipeline
from src.ingestion.pipeline import IngestionPipeline

__all__ = ["DocumentManager", "IngestionPipeline", "build_ingestion_pipeline"]
