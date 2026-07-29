"""文档摄取中的向量化阶段。"""

from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder, tokenize

__all__ = ["DenseEncoder", "SparseEncoder", "tokenize"]
