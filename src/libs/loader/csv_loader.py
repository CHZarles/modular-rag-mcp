"""Normalize CSV rows into self-contained searchable Markdown sections."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from src.core.types import Document
from src.libs.loader.file_integrity import compute_sha256

_DELIMITERS = ",;\t"


class CsvLoader:
    supported_extensions: tuple[str, ...] = (".csv",)
    revision = "stdlib-csv:document-v1"

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        del trace
        path = Path(source_path).expanduser()
        _validate_input(path, collection)
        text = csv_to_markdown(path.read_bytes(), path.stem)
        return Document(
            id=compute_sha256(path),
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "csv",
                "title": path.stem,
                "images": [],
            },
        )


def csv_to_markdown(content: bytes, title: str) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("csv must use UTF-8 encoding") from exc
    if not text or "\0" in text:
        raise ValueError("csv content is empty or binary")

    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=_DELIMITERS)
    except csv.Error:
        dialect = csv.excel
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), dialect))
    except csv.Error as exc:
        raise ValueError("csv content cannot be parsed") from exc
    if not records:
        raise ValueError("csv header is required")

    headers = [header.strip() for header in records[0]]
    if not headers or any(not header for header in headers) or len(set(headers)) != len(headers):
        raise ValueError("csv header names must be non-empty and unique")

    sections: list[str] = []
    for row in records[1:]:
        values = [_cell_text(value) for value in row]
        if not any(values):
            continue
        if len(values) != len(headers):
            raise ValueError("csv rows must match the header column count")
        fields = "\n".join(f"{header}: {value}" for header, value in zip(headers, values))
        sections.append(f"## Row {len(sections) + 1}\n\n{fields}")
    if not sections:
        raise ValueError("csv must contain at least one data row")
    return f"# {title}\n\n" + "\n\n".join(sections)


def _cell_text(value: str) -> str:
    return " ".join(value.split())


def _validate_input(path: Path, collection: str) -> None:
    if path.suffix.lower() != ".csv":
        raise ValueError(f"csv loader input error: unsupported file type {path.suffix!r}")
    if not path.is_file():
        raise FileNotFoundError(f"csv loader input error: file not found: {path}")
    if not collection.strip() or Path(collection).name != collection or collection in {".", ".."}:
        raise ValueError("csv loader input error: collection must be a simple name")


__all__ = ["CsvLoader", "csv_to_markdown"]
