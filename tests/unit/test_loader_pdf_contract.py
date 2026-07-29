from __future__ import annotations

import base64
from pathlib import Path

import pymupdf
import pytest

from libs.loader import BaseLoader, PdfLoader

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def make_pdf(path: Path, with_image: bool = False) -> Path:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Sample PDF Title")
        page.insert_text((72, 96), "Local retrieval content")
        if with_image:
            page.insert_image(pymupdf.Rect(72, 120, 122, 170), stream=_PNG_1X1)
        pdf.save(path)
    return path


def make_repeated_image_pdf(path: Path) -> Path:
    """创建同一图片资源出现两次、且前后都有正文的版面。"""
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Before image")
        xref = page.insert_image(pymupdf.Rect(72, 90, 122, 140), stream=_PNG_1X1)
        page.insert_text((72, 180), "Between images")
        page.insert_image(pymupdf.Rect(72, 190, 122, 240), xref=xref)
        page.insert_text((72, 280), "After images")
        pdf.save(path)
    return path


def test_pdf_loader_returns_document_with_required_metadata(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "simple.pdf")
    loader = PdfLoader(image_root=tmp_path / "images")

    document = loader.load(str(source), "docs")

    assert isinstance(loader, BaseLoader)
    assert "Sample PDF Title" in document.text
    assert document.metadata == {
        "source_path": str(source),
        "collection": "docs",
        "doc_type": "pdf",
        "title": "simple",
        "images": [],
    }
    assert len(document.id) == 64


def test_pdf_loader_extracts_images_and_adds_placeholders(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "with_images.pdf", with_image=True)
    loader = PdfLoader(image_root=tmp_path / "images")

    document = loader.load(str(source), "docs")

    image = document.metadata["images"][0]
    placeholder = f"[IMAGE: {image['id']}]"
    assert document.text[image["text_offset"] :][: image["text_length"]] == placeholder
    assert image["page"] == 1
    assert image["position"] == {"x0": 72.0, "y0": 120.0, "x1": 122.0, "y1": 170.0}
    assert Path(image["path"]).is_file()
    assert Path(image["path"]).suffix == ".png"


def test_pdf_loader_places_every_image_occurrence_in_layout_order(tmp_path: Path) -> None:
    source = make_repeated_image_pdf(tmp_path / "repeated_image.pdf")
    loader = PdfLoader(image_root=tmp_path / "images")

    document = loader.load(str(source), "docs")

    images = document.metadata["images"]
    assert len(images) == 2
    first_placeholder = f"[IMAGE: {images[0]['id']}]"
    second_placeholder = f"[IMAGE: {images[1]['id']}]"
    assert (
        document.text.index("Before image")
        < document.text.index(first_placeholder)
        < document.text.index("Between images")
        < document.text.index(second_placeholder)
        < document.text.index("After images")
    )
    assert images[0]["id"] != images[1]["id"]
    assert images[0]["position"] != images[1]["position"]
    for image in images:
        placeholder = f"[IMAGE: {image['id']}]"
        start = image["text_offset"]
        assert document.text[start : start + image["text_length"]] == placeholder
    assert set(images[0]) == {
        "id",
        "path",
        "page",
        "text_offset",
        "text_length",
        "position",
    }


def test_pdf_loader_keeps_text_when_image_extraction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path / "document.pdf")
    loader = PdfLoader(image_root=tmp_path / "images")

    def fail(*args: object) -> list[dict[str, object]]:
        raise RuntimeError("image decoder failed")

    monkeypatch.setattr(loader, "_extract_images", fail)

    document = loader.load(str(source), "docs")

    assert "Local retrieval content" in document.text
    assert document.metadata["images"] == []


def test_pdf_loader_rejects_missing_or_unsupported_files(tmp_path: Path) -> None:
    loader = PdfLoader(image_root=tmp_path / "images")

    with pytest.raises(ValueError, match="unsupported file type"):
        loader.load(str(tmp_path / "document.txt"), "docs")
    with pytest.raises(FileNotFoundError, match="file not found"):
        loader.load(str(tmp_path / "missing.pdf"), "docs")


def test_pdf_loader_rejects_collection_path_traversal(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "simple.pdf")
    loader = PdfLoader(image_root=tmp_path / "images")

    with pytest.raises(ValueError, match="collection must be a simple name"):
        loader.load(str(source), "../outside")
