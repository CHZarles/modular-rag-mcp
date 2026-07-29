"""基于 SHA256 和 SQLite 的文件增量摄取记录。"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from src.core.types import JsonDict
from src.ports.ingestion import FileIntegrityStore

# 沿用稳定端口作为公开抽象名，避免在 Libs 层维护第二套重复接口。
FileIntegrityChecker = FileIntegrityStore


class SQLiteIntegrityChecker(FileIntegrityStore):
    """记录每个 collection 已处理的文件哈希，避免重复解析和模型调用。

    每次数据库操作都创建短生命周期连接，不在线程间共享连接。SQLite 使用 WAL
    模式和 busy timeout，使多个摄取任务可以并发读写同一个历史库。
    """

    def __init__(
        self,
        db_path: str | Path = "data/db/ingestion_history.db",
        timeout_seconds: float = 30,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("file integrity configuration error: timeout_seconds must be positive")
        self.db_path = Path(db_path).expanduser()
        self.timeout_seconds = timeout_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def compute_sha256(source_path: str) -> str:
        """分块读取文件并返回十六进制 SHA256，避免把大文件一次性载入内存。"""
        digest = hashlib.sha256()
        with Path(source_path).expanduser().open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def should_skip(self, file_hash: str, collection: str) -> bool:
        """仅当同一 collection 中存在 success 记录时跳过该文件。"""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM ingestion_history
                WHERE file_hash = ? AND collection = ? AND status = 'success'
                LIMIT 1
                """,
                (file_hash, collection),
            ).fetchone()
        return row is not None

    def mark_processing(self, file_hash: str, source_path: str, collection: str) -> None:
        """在耗时处理开始前写入 processing 状态。"""
        self._write_status(file_hash, source_path, collection, "processing")

    def mark_success(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        chunk_count: int,
    ) -> None:
        """记录成功状态和最终 chunk 数，使后续摄取可以直接跳过。"""
        if chunk_count < 0:
            raise ValueError("file integrity error: chunk_count must be non-negative")
        self._write_status(
            file_hash,
            source_path,
            collection,
            "success",
            chunk_count=chunk_count,
        )

    def mark_failed(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        error: str,
    ) -> None:
        """记录失败原因；failed 文件下次仍会重新处理。"""
        self._write_status(file_hash, source_path, collection, "failed", error=error)

    def remove_record(self, file_hash: str, collection: str) -> None:
        """移除指定 collection 的历史记录，使文件可以重新摄取。"""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM ingestion_history WHERE file_hash = ? AND collection = ?",
                (file_hash, collection),
            )

    def list_processed(self, collection: str | None = None) -> list[JsonDict]:
        """列出摄取历史；传入 collection 时只返回该集合的数据。"""
        sql = """
            SELECT file_hash, file_path, file_size, collection, status,
                   processed_at, error_msg, chunk_count
            FROM ingestion_history
        """
        parameters: tuple[str, ...] = ()
        if collection is not None:
            sql += " WHERE collection = ?"
            parameters = (collection,)
        sql += " ORDER BY processed_at DESC, file_path ASC"

        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        """创建表和索引，并为数据库启用 WAL 并发模式。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_history (
                    file_hash TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    status TEXT NOT NULL
                        CHECK(status IN ('success', 'failed', 'processing')),
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    error_msg TEXT,
                    chunk_count INTEGER,
                    PRIMARY KEY (file_hash, collection)
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_status
                    ON ingestion_history(status);
                CREATE INDEX IF NOT EXISTS idx_ingestion_processed_at
                    ON ingestion_history(processed_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """创建带行字典和锁等待时间配置的独立连接。"""
        connection = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection

    def _write_status(
        self,
        file_hash: str,
        source_path: str,
        collection: str,
        status: str,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """使用 upsert 原子写入状态，同一文件和集合始终只有一条记录。"""
        path = Path(source_path).expanduser()
        file_size = path.stat().st_size if path.exists() else None
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO ingestion_history (
                    file_hash, collection, file_path, file_size, status,
                    processed_at, error_msg, chunk_count
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                ON CONFLICT(file_hash, collection) DO UPDATE SET
                    file_path = excluded.file_path,
                    file_size = excluded.file_size,
                    status = excluded.status,
                    processed_at = CURRENT_TIMESTAMP,
                    error_msg = excluded.error_msg,
                    chunk_count = excluded.chunk_count
                """,
                (
                    file_hash,
                    collection,
                    source_path,
                    file_size,
                    status,
                    error,
                    chunk_count,
                ),
            )


__all__ = ["FileIntegrityChecker", "SQLiteIntegrityChecker"]
