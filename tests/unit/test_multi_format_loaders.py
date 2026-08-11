from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.core.types import Document
from src.ingestion.factory import _create_format_router
from src.libs.loader.csv_loader import CsvLoader
from src.libs.loader.docx_loader import (
    MAX_DOCX_UNCOMPRESSED_BYTES,
    DocxLoader,
    validate_docx,
)
from src.libs.loader.format_router import SUPPORTED_EXTENSIONS, FormatRouter
from src.libs.loader.image_loader import ImageLoader


class _FakeLoader:
    supported_extensions = (".pdf",)
    revision = "fake-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any | None]] = []

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        self.calls.append((source_path, collection, trace))
        return Document("doc", "text", {"source_path": source_path})


def test_format_router_dispatches_case_insensitive_extension(tmp_path: Path) -> None:
    source = tmp_path / "GUIDE.PDF"
    source.write_bytes(b"pdf")
    loader = _FakeLoader()
    trace = object()
    router = FormatRouter({".pdf": loader})

    document = router.load(str(source), "docs", trace=trace)

    assert document.id == "doc"
    assert loader.calls == [(str(source), "docs", trace)]
    assert router.supported_extensions == (".pdf",)
    assert "fake-v1" in router.revision


def test_format_router_rejects_unknown_extension(tmp_path: Path) -> None:
    router = FormatRouter({".pdf": _FakeLoader()})

    with pytest.raises(ValueError, match="unsupported file type"):
        router.load(str(tmp_path / "guide.txt"), "docs")


def test_ingestion_factory_assembles_all_supported_formats(tmp_path: Path) -> None:
    router = _create_format_router({}, str(tmp_path / "images"))

    assert set(router.supported_extensions) == set(SUPPORTED_EXTENSIONS)


class _Conversion:
    def __init__(self, text: str) -> None:
        self.text_content = text


class _Converter:
    def __init__(self, text: str) -> None:
        self.text = text

    def convert(self, _path: str) -> _Conversion:
        return _Conversion(self.text)


def _docx_bytes(media: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        for name, content in (media or {}).items():
            archive.writestr(f"word/media/{name}", content)
    return output.getvalue()


def test_docx_loader_returns_canonical_document(tmp_path: Path) -> None:
    source = tmp_path / "architecture.docx"
    source.write_bytes(_docx_bytes())
    loader = DocxLoader(converter=_Converter("# System\n\nLoader content."))

    document = loader.load(str(source), "docs")

    assert document.text == "# System\n\nLoader content."
    assert document.metadata == {
        "source_path": str(source),
        "collection": "docs",
        "doc_type": "docx",
        "title": "architecture",
        "images": [],
    }
    assert len(document.id) == 64


def test_docx_loader_extracts_embedded_images_for_captioning(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(image, format="PNG")
    source = tmp_path / "architecture.docx"
    source.write_bytes(_docx_bytes({"image1.png": image.getvalue()}))

    document = DocxLoader(
        image_root=tmp_path / "images",
        converter=_Converter("# System\n\nArchitecture text."),
    ).load(str(source), "docs")

    embedded = document.metadata["images"][0]
    placeholder = f"[IMAGE: {embedded['id']}]"
    assert placeholder in document.text
    assert document.text[embedded["text_offset"] :][: embedded["text_length"]] == placeholder
    assert Path(embedded["path"]).read_bytes() == image.getvalue()
    assert embedded["mime_type"] == "image/png"


@pytest.mark.parametrize(
    ("content", "converter", "message"),
    [
        (b"not a zip", _Converter("text"), "valid ZIP"),
        (_docx_bytes(), _Converter("   "), "produced no text"),
    ],
)
def test_docx_loader_rejects_invalid_or_empty_documents(
    tmp_path: Path,
    content: bytes,
    converter: _Converter,
    message: str,
) -> None:
    source = tmp_path / "invalid.docx"
    source.write_bytes(content)

    with pytest.raises((ValueError, RuntimeError), match=message):
        DocxLoader(converter=converter).load(str(source), "docs")


def test_docx_validation_rejects_excessive_declared_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Info:
        def __init__(self, filename: str, file_size: int) -> None:
            self.filename = filename
            self.file_size = file_size

    class _Archive:
        def __enter__(self) -> _Archive:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def infolist(self) -> list[_Info]:
            return [
                _Info("[Content_Types].xml", 1),
                _Info("word/document.xml", MAX_DOCX_UNCOMPRESSED_BYTES),
            ]

    monkeypatch.setattr("src.libs.loader.docx_loader.zipfile.ZipFile", lambda _source: _Archive())

    with pytest.raises(ValueError, match="uncompressed size"):
        validate_docx(b"zip")
def test_csv_loader_makes_each_row_self_contained(tmp_path: Path) -> None:
    source = tmp_path / "people.csv"
    source.write_text(
        "\ufeffname,role,notes\nAlice,Architect,Platform\nBob,PM,\"Line one\nLine two\"\n",
        encoding="utf-8",
    )

    document = CsvLoader().load(str(source), "docs")

    assert document.text == (
        "# people\n\n"
        "## Row 1\n\nname: Alice\nrole: Architect\nnotes: Platform\n\n"
        "## Row 2\n\nname: Bob\nrole: PM\nnotes: Line one Line two"
    )
    assert document.metadata["doc_type"] == "csv"
    assert document.metadata["images"] == []


@pytest.mark.parametrize("content", ["name,name\nAlice,Admin\n", "name,role\n,\n"])
def test_csv_loader_rejects_ambiguous_or_empty_data(tmp_path: Path, content: str) -> None:
    source = tmp_path / "invalid.csv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        CsvLoader().load(str(source), "docs")


def test_image_loader_creates_managed_reference_and_placeholder(tmp_path: Path) -> None:
    source = tmp_path / "architecture.png"
    Image.new("RGB", (8, 6), "white").save(source)
    loader = ImageLoader(tmp_path / "images")

    document = loader.load(str(source), "docs")

    image = document.metadata["images"][0]
    assert document.text == f"# architecture\n\n[IMAGE: {image['id']}]"
    assert image == {
        "id": document.id,
        "path": str(tmp_path / "images" / "docs" / f"{document.id}.png"),
        "page": None,
        "mime_type": "image/png",
        "text_offset": len("# architecture\n\n"),
        "text_length": len(f"[IMAGE: {document.id}]"),
        "position": {},
    }
    assert Path(image["path"]).read_bytes() == source.read_bytes()
    assert document.metadata["standalone_image"] is True


@pytest.mark.parametrize(
    ("suffix", "image_format", "mime_type"),
    [(".jpg", "JPEG", "image/jpeg"), (".jpeg", "JPEG", "image/jpeg"), (".webp", "WEBP", "image/webp")],
)
def test_image_loader_supports_declared_still_formats(
    tmp_path: Path,
    suffix: str,
    image_format: str,
    mime_type: str,
) -> None:
    source = tmp_path / f"asset{suffix}"
    Image.new("RGB", (8, 6), "white").save(source, format=image_format)

    document = ImageLoader(tmp_path / "images").load(str(source), "docs")

    assert document.metadata["images"][0]["mime_type"] == mime_type


def test_image_loader_rejects_extension_content_mismatch(tmp_path: Path) -> None:
    png = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(png, format="PNG")
    source = tmp_path / "fake.jpg"
    source.write_bytes(png.getvalue())

    with pytest.raises(ValueError, match="does not match"):
        ImageLoader(tmp_path / "images").load(str(source), "docs")


def test_image_loader_rejects_animated_webp(tmp_path: Path) -> None:
    source = tmp_path / "animated.webp"
    frames = [Image.new("RGB", (8, 6), color) for color in ("white", "black")]
    frames[0].save(source, format="WEBP", save_all=True, append_images=frames[1:], duration=100)

    with pytest.raises(ValueError, match="animated images"):
        ImageLoader(tmp_path / "images").load(str(source), "docs")
