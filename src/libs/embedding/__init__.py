"""Embedding 接口的兼容导出。"""

from src.libs.embedding.azure_embedding import AzureOpenAIEmbedding
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.embedding.embedding_factory import EmbeddingFactory, create_embedding
from src.libs.embedding.hash_embedding import HashEmbedding
from src.libs.embedding.ollama_embedding import OllamaEmbedding
from src.libs.embedding.openai_embedding import OpenAIEmbedding

__all__ = [
    "AzureOpenAIEmbedding",
    "BaseEmbedding",
    "EmbeddingFactory",
    "HashEmbedding",
    "OllamaEmbedding",
    "OpenAIEmbedding",
    "create_embedding",
]
