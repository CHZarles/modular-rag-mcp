"""为兼容旧导入路径而重新导出转换器端口。"""

from src.ports.ingestion import BaseTransform

__all__ = ["BaseTransform"]
