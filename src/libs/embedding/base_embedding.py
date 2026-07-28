"""为兼容旧导入路径而重新导出 Embedding 端口。"""

from src.ports.ingestion import BaseEmbedding

__all__ = ["BaseEmbedding"]
