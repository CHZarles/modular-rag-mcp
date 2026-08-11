"""Normalize one DOCX file to the existing Document contract."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from markitdown import MarkItDown

from src.core.types import Document, JsonDict
from src.libs.loader.file_integrity import compute_sha256
from src.libs.loader.image_loader import validate_image, write_managed_image
from src.observability.logger import get_logger

MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_REQUIRED_MEMBERS = frozenset({"[Content_Types].xml", "word/document.xml"})
_MEDIA_PREFIX = "word/media/"

logger = get_logger(__name__)


class DocxLoader:
    supported_extensions: tuple[str, ...] = (".docx",)
    revision = "markitdown-docx-images:document-v2"

    def __init__(
        self,
        converter: Any | None = None,
        image_root: str | Path = "data/images",
    ) -> None:
        self._converter = converter or MarkItDown()
        self.image_root = Path(image_root).expanduser()

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        del trace
        path = Path(source_path).expanduser()
        _validate_input(path, collection)
        content = path.read_bytes()
        validate_docx(content)
        try:
            text = str(self._converter.convert(str(path)).text_content).strip()
        except Exception as exc:
            raise RuntimeError(f"docx loader conversion failed: {path}") from exc
        if not text:
            raise RuntimeError(f"docx loader conversion produced no text: {path}")
        file_hash = compute_sha256(path)
        try:
            images = _extract_images(
                content,
                self.image_root,
                collection,
                file_hash,
            )
        except Exception as exc:
            logger.warning("DOCX image extraction failed for %s: %s", path, exc)
            images = []
        text = _append_image_placeholders(text, images)
        return Document(
            id=file_hash,
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "docx",
                "title": path.stem,
                "images": images,
            },
        )


def _extract_images(
    content: bytes,
    image_root: Path,
    collection: str,
    file_hash: str,
) -> list[JsonDict]:
    images: list[JsonDict] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = sorted(
            (
                info
                for info in archive.infolist()
                if info.filename.startswith(_MEDIA_PREFIX) and not info.is_dir()
            ),
            key=lambda info: info.filename,
        )
        for sequence, info in enumerate(members, start=1):
            raw = archive.read(info)
            try:
                mime_type, extension = validate_image(raw, Path(info.filename).suffix)
            except ValueError:
                continue
            image_id = f"{file_hash}_docx_{sequence}"
            target = image_root / collection / f"{image_id}{extension}"
            write_managed_image(target, raw)
            images.append(
                {
                    "id": image_id,
                    "path": str(target),
                    "page": None,
                    "mime_type": mime_type,
                    "text_offset": 0,
                    "text_length": 0,
                    "position": {},
                }
            )
    return images


def _append_image_placeholders(text: str, images: list[JsonDict]) -> str:
    result = text
    if images:
        result += "\n\n## Document images"
    for image in images:
        placeholder = f"[IMAGE: {image['id']}]"
        result += "\n\n"
        image["text_offset"] = len(result)
        image["text_length"] = len(placeholder)
        result += placeholder
    return result


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
