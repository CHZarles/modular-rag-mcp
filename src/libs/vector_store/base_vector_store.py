"""为兼容旧导入路径而重新导出向量存储端口。"""

from src.ports.ingestion import BaseVectorStore

__all__ = ["BaseVectorStore"]
