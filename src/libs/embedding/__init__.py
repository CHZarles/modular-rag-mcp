"""Embedding 接口的兼容导出。"""

from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.embedding.embedding_factory import EmbeddingFactory, create_embedding

__all__ = ["BaseEmbedding", "EmbeddingFactory", "create_embedding"]
