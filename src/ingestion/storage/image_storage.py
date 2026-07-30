"""使用文件系统和 SQLite 管理按 generation 隔离的 PDF 图片引用。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from src.core.types import ImageRef, JsonDict
from src.ports.ingestion import GenerationStateStore

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATH_LOCKS: dict[str, RLock] = {}
_PATH_LOCKS_GUARD = Lock()


class ImageStorage:
    """保存分代图片引用，并只向查询暴露 active generation。

    图片文件由 Loader 以内容哈希命名，内容相同的 generations 可以安全共享物理文件；
    SQLite 引用以 ``doc_key + generation + image_id`` 隔离，旧 worker 不会覆盖新引用。
    ``generation_store=None`` 仅用于旧索引兼容和独立测试；正式查询必须注入控制面。
    """

    def __init__(
        self,
        db_path: str | Path = "data/db/image_index.db",
        image_root: str | Path = "data/images",
        timeout_seconds: float = 30,
        *,
        generation_store: GenerationStateStore | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("image storage configuration error: timeout_seconds must be positive")
        self.db_path = Path(db_path).expanduser()
        self.image_root = Path(image_root).expanduser()
        self.lock_path = self.image_root / ".image-storage.lock"
        self._thread_lock = _path_lock(self.lock_path)
        self.timeout_seconds = timeout_seconds
        self.generation_store = generation_store
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.image_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_refs(
        self,
        images: list[ImageRef],
        doc_key: str,
        generation: int,
        trace: Any | None = None,
    ) -> None:
        """原子写入一代图片引用；相同 generation 内的 image_id 必须唯一。"""
        _validate_generation_identity(doc_key, generation)
        if not images:
            return

        # 文件存在性检查和引用提交必须与删除物理文件互斥。否则 GC 可能恰好在
        # ``is_file()`` 通过后、SQLite INSERT 前删除图片，留下指向缺失文件的新引用。
        with self._file_lifecycle_lock():
            rows: list[tuple[object, ...]] = []
            seen_ids: set[str] = set()
            for image in images:
                if image.image_id in seen_ids:
                    raise ValueError(
                        f"image storage error: duplicate image id {image.image_id!r}"
                    )
                seen_ids.add(image.image_id)
                rows.append(self._prepare_row(image, doc_key, generation))

            with closing(self._connect()) as connection, connection:
                connection.executemany(
                    """
                    INSERT INTO image_versions (
                        storage_id, image_id, doc_key, generation, file_path,
                        collection, source_path, doc_hash, page_num, mime_type,
                        text_offset, text_length, position_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(storage_id) DO UPDATE SET
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
        """按业务 image_id 返回活跃引用；未绑定状态存储时返回最新 generation。"""
        if not isinstance(image_id, str) or not image_id.strip():
            raise ValueError("image storage error: image_id must be non-empty")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM image_versions
                WHERE image_id = ?
                ORDER BY generation DESC
                """,
                (image_id,),
            ).fetchall()
        rows = self._active_rows(rows)
        return _row_to_image(rows[0]) if rows else None

    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]:
        """返回一个文档当前 active generation 的图片。"""
        _validate_source_path(source_path)
        _validate_collection(collection)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM image_versions
                WHERE source_path = ? AND collection = ?
                ORDER BY page_num IS NULL, page_num, image_id
                """,
                (source_path, collection),
            ).fetchall()
        return [_row_to_image(row) for row in self._active_rows(rows, collection)]

    def list_by_collection(self, collection: str) -> list[ImageRef]:
        """返回 collection 中所有文档的 active generation 图片。"""
        _validate_collection(collection)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM image_versions
                WHERE collection = ?
                ORDER BY page_num IS NULL, page_num, image_id
                """,
                (collection,),
            ).fetchall()
        return [_row_to_image(row) for row in self._active_rows(rows, collection)]

    def delete_generation(self, doc_key: str, generation: int) -> int:
        """精确删除一代图片引用；共享文件没有其他引用时才删除物理文件。"""
        _validate_generation_identity(doc_key, generation)
        with self._file_lifecycle_lock():
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    "SELECT file_path FROM image_versions WHERE doc_key = ? AND generation = ?",
                    (doc_key, generation),
                ).fetchall()
                connection.execute(
                    "DELETE FROM image_versions WHERE doc_key = ? AND generation = ?",
                    (doc_key, generation),
                )
            self._delete_unreferenced_files([str(row["file_path"]) for row in rows])
        return len(rows)

    def delete_by_document(self, source_path: str, collection: str) -> int:
        """删除逻辑文档的所有 generations，供显式文档删除流程使用。"""
        _validate_source_path(source_path)
        _validate_collection(collection)
        with self._file_lifecycle_lock():
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    """
                    SELECT file_path FROM image_versions
                    WHERE source_path = ? AND collection = ?
                    """,
                    (source_path, collection),
                ).fetchall()
                connection.execute(
                    "DELETE FROM image_versions WHERE source_path = ? AND collection = ?",
                    (source_path, collection),
                )
            self._delete_unreferenced_files([str(row["file_path"]) for row in rows])
        return len(rows)

    @contextmanager
    def _file_lifecycle_lock(self):
        """跨线程、跨进程串行化“校验并引用”和“取消引用并删文件”。"""
        self.image_root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _active_rows(
        self,
        rows: list[sqlite3.Row],
        collection: str | None = None,
    ) -> list[sqlite3.Row]:
        if self.generation_store is None:
            return rows
        active = self.generation_store.get_active_generations(collection)
        return [
            row
            for row in rows
            if active.get(str(row["doc_key"])) == int(row["generation"])
        ]

    def _prepare_row(
        self,
        image: ImageRef,
        doc_key: str,
        generation: int,
    ) -> tuple[object, ...]:
        if not image.image_id.strip():
            raise ValueError("image storage error: image_id must be non-empty")
        _validate_collection(image.collection)
        _validate_source_path(image.source_path)
        if image.page is not None and (
            not isinstance(image.page, int) or isinstance(image.page, bool) or image.page <= 0
        ):
            raise ValueError("image storage error: page must be positive")
        for name, value in (("text_offset", image.text_offset), ("text_length", image.text_length)):
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
        storage_id = hashlib.sha256(
            f"{doc_key}\0{generation}\0{image.image_id}".encode()
        ).hexdigest()
        return (
            storage_id,
            image.image_id,
            doc_key,
            generation,
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

    def _delete_unreferenced_files(self, raw_paths: list[str]) -> None:
        for raw_path in set(raw_paths):
            with closing(self._connect()) as connection:
                reference = connection.execute(
                    "SELECT 1 FROM image_versions WHERE file_path = ? LIMIT 1",
                    (raw_path,),
                ).fetchone()
            if reference is None:
                path = Path(raw_path)
                # 数据库中保存的路径已经在 save_refs 时完成受管目录校验。
                path.unlink(missing_ok=True)

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
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS image_versions (
                    storage_id TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL,
                    doc_key TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    doc_hash TEXT,
                    page_num INTEGER,
                    mime_type TEXT NOT NULL,
                    text_offset INTEGER,
                    text_length INTEGER,
                    position_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(doc_key, generation, image_id)
                );
                CREATE INDEX IF NOT EXISTS idx_image_version_id
                    ON image_versions(image_id);
                CREATE INDEX IF NOT EXISTS idx_image_version_collection
                    ON image_versions(collection);
                CREATE INDEX IF NOT EXISTS idx_image_version_document
                    ON image_versions(source_path, collection);
                CREATE INDEX IF NOT EXISTS idx_image_version_generation
                    ON image_versions(doc_key, generation);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection


_SELECT_COLUMNS = (
    "storage_id, image_id, doc_key, generation, file_path, collection, source_path, "
    "page_num, mime_type, text_offset, text_length, position_json"
)


def _validate_generation_identity(doc_key: str, generation: int) -> None:
    if not isinstance(doc_key, str) or not doc_key.strip():
        raise ValueError("image storage error: doc_key must be non-empty")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        raise ValueError("image storage error: generation must be a positive integer")


def _path_lock(path: Path) -> RLock:
    """让同一进程内指向同一图片根目录的实例复用一把可重入锁。"""
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, RLock())


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
