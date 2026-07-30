from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from core.types import ImageRef
from ingestion.storage import ImageStorage
from ports.ingestion import ImageStore


def make_image(
    image_root: Path,
    image_id: str,
    *,
    collection: str = "docs",
    source_path: str = "documents/guide.pdf",
    page: int = 1,
) -> ImageRef:
    path = image_root / collection / f"{image_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png-data")
    return ImageRef(
        image_id=image_id,
        path=str(path),
        collection=collection,
        source_path=source_path,
        page=page,
        mime_type="image/png",
        text_offset=12,
        text_length=24,
        position={"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0},
    )


def test_defaults_create_sqlite_index_with_wal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    storage = ImageStorage()

    assert storage.db_path == Path("data/db/image_index.db")
    assert storage.image_root == Path("data/images")
    assert storage.db_path.is_file()
    with sqlite3.connect(storage.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(storage, ImageStore)


def test_save_refs_roundtrips_all_fields_and_persists_doc_hash(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    db_path = tmp_path / "image_index.db"
    document_hash = "a" * 64
    image = make_image(image_root, f"{document_hash}_2_1", page=2)

    ImageStorage(db_path, image_root).save_refs([image])
    restored = ImageStorage(db_path, image_root).get(image.image_id)

    assert restored is not None
    assert restored.to_dict() == replace(image, path=str(Path(image.path).resolve())).to_dict()
    assert Path(restored.path).is_file()
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT doc_hash, page_num FROM image_index WHERE image_id = ?",
            (image.image_id,),
        ).fetchone()
    assert row == (document_hash, 2)


def test_upsert_is_idempotent_and_queries_are_scoped(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    storage = ImageStorage(tmp_path / "image_index.db", image_root)
    first = make_image(image_root, "image-a", source_path="documents/a.pdf", page=1)
    second = make_image(image_root, "image-b", source_path="documents/a.pdf", page=2)
    another_document = make_image(
        image_root,
        "image-c",
        source_path="documents/b.pdf",
        page=1,
    )
    another_collection = make_image(
        image_root,
        "image-d",
        collection="notes",
        source_path="documents/a.pdf",
        page=1,
    )

    storage.save_refs([first, second, another_document, another_collection])
    storage.save_refs([replace(first, page=3)])

    restored = storage.get(first.image_id)
    assert restored is not None
    assert restored.to_dict() == replace(
        first,
        path=str(Path(first.path).resolve()),
        page=3,
    ).to_dict()
    assert [image.image_id for image in storage.list_by_document("documents/a.pdf", "docs")] == [
        "image-b",
        "image-a",
    ]
    assert [image.image_id for image in storage.list_by_collection("docs")] == [
        "image-c",
        "image-b",
        "image-a",
    ]


def test_invalid_batch_is_rejected_before_any_mapping_is_written(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    storage = ImageStorage(tmp_path / "image_index.db", image_root)
    valid = make_image(image_root, "valid")
    missing = replace(valid, image_id="missing", path=str(image_root / "docs/missing.png"))

    with pytest.raises(ValueError, match="file must exist"):
        storage.save_refs([valid, missing])
    assert storage.get(valid.image_id) is None

    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="must be inside"):
        storage.save_refs([replace(valid, image_id="outside", path=str(outside))])
    with pytest.raises(ValueError, match="duplicate image id"):
        storage.save_refs([valid, valid])
    with pytest.raises(ValueError, match="page must be positive"):
        storage.save_refs([replace(valid, page=0)])


def test_delete_by_document_removes_only_matching_rows_and_files(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    storage = ImageStorage(tmp_path / "image_index.db", image_root)
    first = make_image(image_root, "image-a", source_path="documents/a.pdf", page=1)
    second = make_image(image_root, "image-b", source_path="documents/a.pdf", page=2)
    other_document = make_image(
        image_root,
        "image-c",
        source_path="documents/b.pdf",
    )
    other_collection = make_image(
        image_root,
        "image-d",
        collection="notes",
        source_path="documents/a.pdf",
    )
    storage.save_refs([first, second, other_document, other_collection])

    removed = storage.delete_by_document("documents/a.pdf", "docs")

    assert removed == 2
    assert storage.delete_by_document("documents/a.pdf", "docs") == 0
    assert storage.get(first.image_id) is None
    assert storage.get(second.image_id) is None
    assert not Path(first.path).exists()
    assert not Path(second.path).exists()
    assert Path(other_document.path).is_file()
    assert Path(other_collection.path).is_file()


def test_concurrent_writes_keep_all_image_mappings(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    storage = ImageStorage(tmp_path / "image_index.db", image_root)
    images = [make_image(image_root, f"image-{index}", page=index + 1) for index in range(12)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda image: storage.save_refs([image]), images))

    assert len(storage.list_by_collection("docs")) == len(images)
