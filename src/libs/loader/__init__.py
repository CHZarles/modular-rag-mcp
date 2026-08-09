"""文档加载器接口的兼容导出。"""

from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.file_integrity import FileIntegrityStore, SQLiteIntegrityStore
from src.libs.loader.mineru_pdf_loader import MinerUPdfLoader
from src.libs.loader.pdf_loader import PdfLoader

__all__ = [
    "BaseLoader",
    "FileIntegrityStore",
    "MinerUPdfLoader",
    "PdfLoader",
    "SQLiteIntegrityStore",
]
