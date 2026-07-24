"""PDF Loader — uses ``pypdf`` for real PDFs, with a text fallback for tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.types import Document
from src.ports.ingestion import BaseLoader


class PDFLoader(BaseLoader):
    """Parse a PDF file into a ``Document`` with per-page text + image refs.

    Falls back to a plain-text read when ``pypdf`` is unavailable so the
    rest of the pipeline can still be exercised against ``.txt`` fixtures
    in tests.
    """

    supported_extensions = (".pdf", ".txt")

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF loader: source not found: {path}")

        suffix = path.suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError(
                f"PDF loader: unsupported extension {suffix!r}; "
                f"expected one of {self.supported_extensions}"
            )
        text = self._extract_text(path, suffix)
        images = self._extract_images(path, suffix)
        return Document(
            id=path.stem,
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "loader": "pdf",
                "images": images,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(path: Path, suffix: str) -> str:
        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="replace")
        try:
            from pypdf import PdfReader  # local import — heavy dep, optional
        except ImportError:
            return path.read_text(encoding="utf-8", errors="replace")

        reader = PdfReader(str(path))
        pages: list[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            pages.append(f"[Page {idx}]\n{page_text}")
        return "\n\n".join(pages)

    @staticmethod
    def _extract_images(path: Path, suffix: str) -> list[dict[str, Any]]:
        # The actual image extraction (and writing them to disk) is
        # delegated to C13's ImageStorage; this loader only records
        # metadata so downstream Transforms can decide what to caption.
        if suffix == ".txt":
            return []
        try:
            from pypdf import PdfReader  # noqa: F401
        except ImportError:
            return []
        return []  # PDF image extraction is plugged in at C13.