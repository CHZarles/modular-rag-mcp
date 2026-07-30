"""使用本地文件系统和 SQLite 管理 PDF 图片引用。

``PdfLoader`` 已负责把图片文件写入 ``data/images/{collection}/``；本模块不重复
解码或复制图片，而是在写入索引前确认文件属于受管目录，并持久化 ``ImageRef``。
SQLite 只保存图片到文档的映射，真实图片仍保存在文件系统中。
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from src.core.types import ImageRef, JsonDict

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ImageStorage:
    """持久化图片引用，并按图片、collection 或源文档查询和删除。

    每次操作创建独立 SQLite 连接，避免在线程间共享连接。数据库启用 WAL 和
    busy timeout，适合当前本地多线程摄取；文件系统与 SQLite 之间不声称事务性。
    """

    def __init__(
        self,
        db_path: str | Path = "data/db/image_index.db",
        image_root: str | Path = "data/images",
        timeout_seconds: float = 30,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("image storage configuration error: timeout_seconds must be positive")

        self.db_path = Path(db_path).expanduser()
        self.image_root = Path(image_root).expanduser()
        self.timeout_seconds = timeout_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.image_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_refs(self, images: list[ImageRef], trace: Any | None = None) -> None:
        """原子写入一批已落盘图片的引用；相同 ID 使用 upsert 更新。"""
        rows: list[tuple[object, ...]] = []
        seen_ids: set[str] = set()
        for image in images:
            if image.image_id in seen_ids:
                raise ValueError(f"image storage error: duplicate image id {image.image_id!r}")
            seen_ids.add(image.image_id)
            rows.append(self._prepare_row(image))
        if not rows:
            return

        # 先完成整批校验，再开启写事务，避免部分合法记录提前进入索引。
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO image_index (
                    image_id, file_path, collection, source_path, doc_hash,
                    page_num, mime_type, text_offset, text_length, position_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    collection = excluded.collection,
                    source_path = excluded.source_path,
                    doc_hash = excluded.doc_hash,
                    page_num = excluded.page_num,
                    mime_type = excluded.mime_type,
                    text_offset = excluded.text_offset,
                    text_length = excluded.text_length,
                    position_json = excluded.position_json
                """,
                rows,
            )

    def get(self, image_id: str) -> ImageRef | None:
        """按 image_id 返回完整图片引用；不存在时返回 ``None``。"""
        if not isinstance(image_id, str) or not image_id.strip():
            raise ValueError("image storage error: image_id must be non-empty")
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM image_index WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        return _row_to_image(row) if row is not None else None

    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]:
        """按源文件和 collection 返回图片，页码相同时按 ID 稳定排序。"""
        _validate_source_path(source_path)
        _validate_collection(collection)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM image_index
                WHERE source_path = ? AND collection = ?
                ORDER BY page_num IS NULL, page_num, image_id
                """,
                (source_path, collection),
            ).fetchall()
        return [_row_to_image(row) for row in rows]

    def list_by_collection(self, collection: str) -> list[ImageRef]:
        """返回指定 collection 的全部图片引用。"""
        _validate_collection(collection)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM image_index
                WHERE collection = ?
                ORDER BY page_num IS NULL, page_num, image_id
                """,
                (collection,),
            ).fetchall()
        return [_row_to_image(row) for row in rows]

    def delete_by_document(self, source_path: str, collection: str) -> int:
        """删除一个文档的索引行和图片文件，返回删除的映射数量。

        先提交 SQLite 删除，再清理文件。若文件清理失败，最坏结果是留下不再被引用
        的孤立文件，不会留下指向缺失图片的有效索引行。
        """
        _validate_source_path(source_path)
        _validate_collection(collection)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT file_path
                FROM image_index
                WHERE source_path = ? AND collection = ?
                """,
                (source_path, collection),
            ).fetchall()
            paths = [
                self._managed_path(str(row["file_path"]), collection, must_exist=False)
                for row in rows
            ]
            connection.execute(
                "DELETE FROM image_index WHERE source_path = ? AND collection = ?",
                (source_path, collection),
            )

        for path in paths:
            path.unlink(missing_ok=True)
        return len(rows)

    def _prepare_row(self, image: ImageRef) -> tuple[object, ...]:
        if not image.image_id.strip():
            raise ValueError("image storage error: image_id must be non-empty")
        _validate_collection(image.collection)
        _validate_source_path(image.source_path)
        if image.page is not None and (
            not isinstance(image.page, int) or isinstance(image.page, bool) or image.page <= 0
        ):
            raise ValueError("image storage error: page must be positive")
        for name, value in (
            ("text_offset", image.text_offset),
            ("text_length", image.text_length),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"image storage error: {name} must be non-negative")
        if not image.mime_type.strip():
            raise ValueError("image storage error: mime_type must be non-empty")
        try:
            position_json = json.dumps(image.position, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("image storage error: position must be JSON serializable") from exc

        path = self._managed_path(image.path, image.collection, must_exist=True)
        return (
            image.image_id,
            str(path),
            image.collection,
            image.source_path,
            _extract_doc_hash(image.image_id),
            image.page,
            image.mime_type,
            image.text_offset,
            image.text_length,
            position_json,
        )

    def _managed_path(self, raw_path: str, collection: str, *, must_exist: bool) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("image storage error: file path must be non-empty")
        path = Path(raw_path).expanduser()
        if must_exist and not path.is_file():
            raise ValueError(f"image storage error: image file must exist: {path}")

        resolved = path.resolve()
        collection_root = (self.image_root / collection).resolve()
        try:
            resolved.relative_to(collection_root)
        except ValueError as exc:
            raise ValueError(
                f"image storage error: image file must be inside {collection_root}"
            ) from exc
        return resolved

    def _initialize(self) -> None:
        """创建图片映射表和查询索引，并启用 SQLite WAL。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS image_index (
                    image_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    doc_hash TEXT,
                    page_num INTEGER,
                    mime_type TEXT NOT NULL,
                    text_offset INTEGER,
                    text_length INTEGER,
                    position_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_image_collection
                    ON image_index(collection);
                CREATE INDEX IF NOT EXISTS idx_image_doc_hash
                    ON image_index(doc_hash);
                CREATE INDEX IF NOT EXISTS idx_image_document
                    ON image_index(source_path, collection);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection


_SELECT_COLUMNS = (
    "image_id, file_path, collection, source_path, page_num, "
    "mime_type, text_offset, text_length, position_json"
)


def _validate_collection(collection: str) -> None:
    if (
        not isinstance(collection, str)
        or not collection.strip()
        or Path(collection).name != collection
        or collection in {".", ".."}
    ):
        raise ValueError("image storage error: collection must be a simple name")


def _validate_source_path(source_path: str) -> None:
    if not isinstance(source_path, str) or not source_path.strip():
        raise ValueError("image storage error: source_path must be non-empty")


def _extract_doc_hash(image_id: str) -> str | None:
    prefix = image_id.partition("_")[0].lower()
    return prefix if _SHA256.fullmatch(prefix) else None


def _row_to_image(row: sqlite3.Row) -> ImageRef:
    position = _decode_position(row["position_json"], str(row["image_id"]))
    return ImageRef(
        image_id=str(row["image_id"]),
        path=str(row["file_path"]),
        collection=str(row["collection"]),
        source_path=str(row["source_path"]),
        page=row["page_num"],
        mime_type=str(row["mime_type"]),
        text_offset=row["text_offset"],
        text_length=row["text_length"],
        position=position,
    )


def _decode_position(raw: object, image_id: str) -> JsonDict:
    if not isinstance(raw, str):
        raise ValueError(f"image index read error: image {image_id!r} position is invalid")
    try:
        position = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"image index read error: image {image_id!r} position is invalid") from exc
    if not isinstance(position, dict):
        raise ValueError(f"image index read error: image {image_id!r} position is invalid")
    return position


__all__ = ["ImageStorage"]
