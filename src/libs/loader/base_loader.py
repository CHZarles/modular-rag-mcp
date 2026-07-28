"""为兼容旧导入路径而重新导出文档加载器端口。"""

from src.ports.ingestion import BaseLoader

__all__ = ["BaseLoader"]
