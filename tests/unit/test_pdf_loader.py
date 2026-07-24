"""Tests for C3: PDF Loader."""

from __future__ import annotations

import pytest

from src.libs.loader.pdf_loader import PDFLoader
from src.ports.ingestion import BaseLoader


def test_loader_is_base_loader_instance() -> None:
    loader = PDFLoader()
    assert isinstance(loader, BaseLoader)
    assert ".pdf" in loader.supported_extensions
    assert ".txt" in loader.supported_extensions


def test_loads_txt_file_with_basic_metadata(tmp_path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("# Title\n\nSome body content.", encoding="utf-8")

    loader = PDFLoader()
    doc = loader.load(str(p), collection="docs")

    assert doc.id == "doc"
    assert "Title" in doc.text
    assert doc.metadata["source_path"] == str(p)
    assert doc.metadata["collection"] == "docs"
    assert doc.metadata["images"] == []


def test_load_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        PDFLoader().load(str(tmp_path / "missing.pdf"), collection="docs")


def test_unsupported_extension_raises(tmp_path) -> None:
    p = tmp_path / "img.xyz"
    p.write_text("nonsense")
    with pytest.raises(ValueError, match="unsupported extension"):
        PDFLoader().load(str(p), collection="docs")


def test_loads_real_pdf_when_pypdf_available(tmp_path):
    """Skip if pypdf isn't installed; otherwise build a minimal PDF and round-trip."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    pdf_path = tmp_path / "tiny.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)

    loader = PDFLoader()
    doc = loader.load(str(pdf_path), collection="docs")
    assert "Page 1" in doc.text
    assert doc.metadata["collection"] == "docs"