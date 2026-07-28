"""为兼容旧导入路径而重新导出文本切分器端口。"""

from src.ports.ingestion import BaseSplitter

__all__ = ["BaseSplitter"]
