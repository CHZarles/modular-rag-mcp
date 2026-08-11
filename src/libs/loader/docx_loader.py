"""Normalize one DOCX file to the existing Document contract."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from markitdown import MarkItDown

from src.core.types import Document
from src.libs.loader.file_integrity import compute_sha256

MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_REQUIRED_MEMBERS = frozenset({"[Content_Types].xml", "word/document.xml"})


class DocxLoader:
    supported_extensions: tuple[str, ...] = (".docx",)
    revision = "markitdown-docx:document-v1"

    def __init__(self, converter: Any | None = None) -> None:
        self._converter = converter or MarkItDown()

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        del trace
        path = Path(source_path).expanduser()
        _validate_input(path, collection)
        validate_docx(path.read_bytes())
        try:
            text = str(self._converter.convert(str(path)).text_content).strip()
        except Exception as exc:
            raise RuntimeError(f"docx loader conversion failed: {path}") from exc
        if not text:
            raise RuntimeError(f"docx loader conversion produced no text: {path}")
        return Document(
            id=compute_sha256(path),
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "docx",
                "title": path.stem,
                "images": [],
            },
        )


def validate_docx(content: bytes) -> None:
    """Reject malformed or dangerously expanded DOCX containers."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not _REQUIRED_MEMBERS.issubset(info.filename for info in infos):
                raise ValueError("docx is missing required members")
            if sum(info.file_size for info in infos) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ValueError("docx exceeds the uncompressed size limit")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("docx is not a valid ZIP container") from exc


def _validate_input(path: Path, collection: str) -> None:
    if path.suffix.lower() != ".docx":
        raise ValueError(f"docx loader input error: unsupported file type {path.suffix!r}")
    if not path.is_file():
        raise FileNotFoundError(f"docx loader input error: file not found: {path}")
    if not collection.strip() or Path(collection).name != collection or collection in {".", ".."}:
        raise ValueError("docx loader input error: collection must be a simple name")


__all__ = ["DocxLoader", "MAX_DOCX_UNCOMPRESSED_BYTES", "validate_docx"]
