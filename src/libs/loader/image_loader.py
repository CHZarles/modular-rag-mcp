"""Normalize one standalone image into searchable text and an ImageRef payload."""

from __future__ import annotations

import io
import os
import warnings
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from PIL import Image, UnidentifiedImageError

from src.core.types import Document
from src.libs.loader.file_integrity import compute_sha256

_IMAGE_FORMATS = {
    ".png": ("PNG", "image/png", ".png"),
    ".jpg": ("JPEG", "image/jpeg", ".jpg"),
    ".jpeg": ("JPEG", "image/jpeg", ".jpg"),
    ".webp": ("WEBP", "image/webp", ".webp"),
}


class ImageLoader:
    supported_extensions = tuple(_IMAGE_FORMATS)
    revision = "pillow-standalone-image:document-v1"

    def __init__(self, image_root: str | Path = "data/images") -> None:
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
        mime_type, extension = validate_image(content, path.suffix)
        file_hash = compute_sha256(path)
        target = self.image_root / collection / f"{file_hash}{extension}"
        write_managed_image(target, content)

        placeholder = f"[IMAGE: {file_hash}]"
        prefix = f"# {path.stem}\n\n"
        return Document(
            id=file_hash,
            text=prefix + placeholder,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "image",
                "title": path.stem,
                "standalone_image": True,
                "images": [
                    {
                        "id": file_hash,
                        "path": str(target),
                        "page": None,
                        "mime_type": mime_type,
                        "text_offset": len(prefix),
                        "text_length": len(placeholder),
                        "position": {},
                    }
                ],
            },
        )


def validate_image(content: bytes, suffix: str) -> tuple[str, str]:
    expected = _IMAGE_FORMATS.get(suffix.lower())
    if expected is None:
        raise ValueError(f"unsupported image type {suffix!r}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                actual_format = image.format
                frame_count = int(getattr(image, "n_frames", 1))
                image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("image content is invalid") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError("image exceeds the safe pixel limit") from exc
    if actual_format != expected[0]:
        raise ValueError("image content does not match its file extension")
    if frame_count != 1:
        raise ValueError("animated images are not supported")
    return expected[1], expected[2]


def write_managed_image(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _validate_input(path: Path, collection: str) -> None:
    if path.suffix.lower() not in _IMAGE_FORMATS:
        raise ValueError(f"image loader input error: unsupported file type {path.suffix!r}")
    if not path.is_file():
        raise FileNotFoundError(f"image loader input error: file not found: {path}")
    if not collection.strip() or Path(collection).name != collection or collection in {".", ".."}:
        raise ValueError("image loader input error: collection must be a simple name")


__all__ = ["ImageLoader", "validate_image", "write_managed_image"]
