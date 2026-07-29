"""文档加载器接口的兼容导出。"""

from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.file_integrity import FileIntegrityStore, SQLiteIntegrityStore

__all__ = ["BaseLoader", "FileIntegrityStore", "SQLiteIntegrityStore"]
