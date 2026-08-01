"""基于 SQLite 的文档分代领取、租约、围栏与原子发布。

控制面使用稳定 ``doc_key`` 标识逻辑文档，使用 ``source_revision`` 标识本次读取的
内容。每次成功领取都会分配只增不减的 generation 和随机 token；外部存储把 generation
写入记录身份，最终由 ``publish()`` 条件更新唯一的 ``active_generation`` 指针。

SQLite 事务只覆盖领取、续租和发布等极短控制操作，不覆盖 PDF 解析、模型调用或索引
写入。即使旧 worker 在被接管后继续运行，也无法使用旧 generation/token 发布结果。
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from src.core.types import ClaimHandle, ClaimResult, JsonDict
from src.ports.ingestion import FileIntegrityStore


def compute_sha256(source_path: str | Path) -> str:
    """分块读取文件并返回十六进制 SHA256。"""
    digest = hashlib.sha256()
    with Path(source_path).expanduser().open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_source_path(source_path: str | Path) -> str:
    """把用户路径转换为稳定绝对路径，统一相对路径和符号链接身份。"""
    raw = str(source_path)
    if not raw.strip():
        raise ValueError("file integrity error: source_path must be non-empty")
    return str(Path(raw).expanduser().resolve(strict=False))


def compute_doc_key(source_path: str | Path, collection: str) -> str:
    """根据规范化路径和 collection 计算稳定逻辑文档身份。"""
    _validate_collection(collection)
    identity = f"{normalize_source_path(source_path)}\0{collection}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class SQLiteIntegrityStore(FileIntegrityStore):
    """使用 SQLite 保存文档发布指针和每一代摄取尝试。

    ``document_state`` 每个逻辑文档一行，只保存当前公开版本和当前领取；
    ``ingestion_attempt`` 每个 generation 一行，保留领取、失败、围栏和发布历史。
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
        return compute_sha256(source_path)

    @staticmethod
    def compute_doc_key(source_path: str, collection: str) -> str:
        return compute_doc_key(source_path, collection)

    def try_claim(
        self,
        source_revision: str,
        source_path: str,
        collection: str,
        lease_owner: str,
        lease_seconds: float,
        *,
        force: bool = False,
    ) -> ClaimResult:
        """原子领取一个文档 generation。

        对同一个 ``doc_key``，检查有效租约、围栏过期尝试、判断成功跳过和分配新一代都在
        同一个 ``BEGIN IMMEDIATE`` 事务中完成。并发领取者会依次观察前一个事务的新状态。
        """
        if not source_revision.strip():
            raise ValueError("file integrity error: source_revision must be non-empty")
        if not lease_owner.strip():
            raise ValueError("file integrity error: lease_owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("file integrity error: lease_seconds must be positive")
        _validate_collection(collection)

        normalized_path = normalize_source_path(source_path)
        # doc_key 只回答“是哪一个逻辑文档”，不会随文件内容变化；source_revision 则只
        # 回答“本次快照是哪份内容”。两者分离后，同一路径 V1/V2 仍竞争同一领取槽位。
        doc_key = compute_doc_key(normalized_path, collection)
        path = Path(normalized_path)
        file_size = path.stat().st_size if path.exists() else None
        now = time.time()
        lease_expires_at = now + lease_seconds
        handle: ClaimHandle | None = None

        # closing() 负责最终关闭连接，``with connection`` 负责异常回滚/正常提交；进入
        # connection 上下文本身不会执行 BEGIN。这里显式使用 BEGIN IMMEDIATE，让同一
        # SQLite 文件的并发领取者先争夺 RESERVED 写锁，再读取并修改状态，避免两个 worker
        # 都基于同一旧状态完成“先 SELECT、后 UPDATE”。busy_timeout 会等待短暂锁竞争。
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            # document_state 每个 doc_key 只有一行：active_* 是公开版本，claimed_* 是
            # 当前制作版本。ingestion_attempt 的完整历史不会参与本次所有权判断。
            state = connection.execute(
                """
                SELECT active_revision, last_generation, claimed_generation,
                       claim_token, lease_expires_at
                FROM document_state
                WHERE doc_key = ?
                """,
                (doc_key,),
            ).fetchone()

            if state is not None and state["claimed_generation"] is not None:
                expiry = state["lease_expires_at"]
                lease_is_active = (
                    isinstance(expiry, int | float)
                    and not isinstance(expiry, bool)
                    and float(expiry) > now
                )
                if lease_is_active:
                    # force 也不能偷走仍有效的领取；调用方把它当作正常 skipped 控制流。
                    return ClaimResult(status="in_progress")

                # 租约过期本身不会杀死旧 worker。新领取事务先把旧 attempt 标为 fenced，
                # 再替换 document_state 中的 generation/claim_token，旧凭证从此无法发布。
                connection.execute(
                    """
                    UPDATE ingestion_attempt
                    SET status = 'fenced', finished_at = CURRENT_TIMESTAMP
                    WHERE doc_key = ? AND generation = ?
                      AND claim_token = ? AND status IN ('claimed', 'staged')
                    """,
                    (doc_key, state["claimed_generation"], state["claim_token"]),
                )
                connection.execute(
                    """
                    UPDATE document_state
                    SET claimed_generation = NULL,
                        claimed_revision = NULL,
                        claim_token = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE doc_key = ?
                    """,
                    (doc_key,),
                )

            if state is not None and state["active_revision"] == source_revision and not force:
                # 已公开同一份快照时不创建新 attempt，也不消耗 generation。
                return ClaimResult(status="already_succeeded")

            # last_generation 即使失败也不回退、不复用。generation 负责物理隔离，随机
            # claim_token 负责证明“仍是这次领取”；后续 CAS 必须同时匹配二者。
            generation = (int(state["last_generation"]) if state is not None else 0) + 1
            claim_token = uuid4().hex
            if state is None:
                connection.execute(
                    """
                    INSERT INTO document_state (
                        doc_key, collection, source_path, file_size, last_generation,
                        claimed_generation, claimed_revision, claim_token,
                        lease_owner, lease_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_key,
                        collection,
                        normalized_path,
                        file_size,
                        generation,
                        generation,
                        source_revision,
                        claim_token,
                        lease_owner,
                        lease_expires_at,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE document_state
                    SET collection = ?, source_path = ?, file_size = ?,
                        last_generation = ?, claimed_generation = ?,
                        claimed_revision = ?, claim_token = ?, lease_owner = ?,
                        lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE doc_key = ?
                    """,
                    (
                        collection,
                        normalized_path,
                        file_size,
                        generation,
                        generation,
                        source_revision,
                        claim_token,
                        lease_owner,
                        lease_expires_at,
                        doc_key,
                    ),
                )

            connection.execute(
                """
                INSERT INTO ingestion_attempt (
                    doc_key, generation, source_revision, claim_token,
                    lease_owner, lease_expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'claimed')
                """,
                (
                    doc_key,
                    generation,
                    source_revision,
                    claim_token,
                    lease_owner,
                    lease_expires_at,
                ),
            )
            handle = ClaimHandle(
                doc_key=doc_key,
                generation=generation,
                source_revision=source_revision,
                claim_token=claim_token,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )

        # 走到这里说明事务已经提交。只有提交之后才把凭证交给 Pipeline 写外部索引。
        return ClaimResult(status="acquired", handle=handle)

    def renew_lease(self, claim: ClaimHandle, lease_seconds: float) -> ClaimHandle:
        """仅在租约仍有效且凭证仍是当前领取时续期。"""
        if lease_seconds <= 0:
            raise ValueError("file integrity error: lease_seconds must be positive")
        now = time.time()
        new_expiry = now + lease_seconds
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE document_state
                SET lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE doc_key = ? AND claimed_generation = ? AND claim_token = ?
                  AND lease_expires_at > ?
                """,
                (new_expiry, claim.doc_key, claim.generation, claim.claim_token, now),
            )
            if cursor.rowcount != 1:
                raise _lost_claim(claim)
            connection.execute(
                """
                UPDATE ingestion_attempt SET lease_expires_at = ?
                WHERE doc_key = ? AND generation = ? AND claim_token = ?
                """,
                (new_expiry, claim.doc_key, claim.generation, claim.claim_token),
            )
        return ClaimHandle(
            doc_key=claim.doc_key,
            generation=claim.generation,
            source_revision=claim.source_revision,
            claim_token=claim.claim_token,
            lease_owner=claim.lease_owner,
            lease_expires_at=new_expiry,
        )

    def mark_staged(self, claim: ClaimHandle) -> None:
        """确认本代所有外部结果已经持久化，但尚未对查询公开。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT 1 FROM document_state
                WHERE doc_key = ? AND claimed_generation = ? AND claim_token = ?
                """,
                (claim.doc_key, claim.generation, claim.claim_token),
            ).fetchone()
            if current is None:
                raise _lost_claim(claim)
            cursor = connection.execute(
                """
                UPDATE ingestion_attempt SET status = 'staged'
                WHERE doc_key = ? AND generation = ? AND claim_token = ?
                  AND status = 'claimed'
                """,
                (claim.doc_key, claim.generation, claim.claim_token),
            )
            if cursor.rowcount != 1:
                raise _lost_claim(claim)

    def publish(self, claim: ClaimHandle, chunk_count: int) -> None:
        """通过 generation/claim_token CAS 原子切换公开版本指针。"""
        if chunk_count < 0:
            raise ValueError("file integrity error: chunk_count must be non-negative")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE document_state
                SET active_generation = ?, active_revision = ?,
                    claimed_generation = NULL, claimed_revision = NULL,
                    claim_token = NULL, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE doc_key = ? AND claimed_generation = ? AND claim_token = ?
                """,
                (
                    claim.generation,
                    claim.source_revision,
                    claim.doc_key,
                    claim.generation,
                    claim.claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise _lost_claim(claim)
            attempt = connection.execute(
                """
                UPDATE ingestion_attempt
                SET status = 'published', chunk_count = ?, finished_at = CURRENT_TIMESTAMP
                WHERE doc_key = ? AND generation = ? AND claim_token = ?
                  AND status = 'staged'
                """,
                (chunk_count, claim.doc_key, claim.generation, claim.claim_token),
            )
            if attempt.rowcount != 1:
                raise RuntimeError("file integrity error: generation must be staged before publish")

    def mark_failed(self, claim: ClaimHandle, error: str) -> None:
        """结束当前失败尝试，但保持上一代 active_generation 不变。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE document_state
                SET claimed_generation = NULL, claimed_revision = NULL,
                    claim_token = NULL, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE doc_key = ? AND claimed_generation = ? AND claim_token = ?
                """,
                (claim.doc_key, claim.generation, claim.claim_token),
            )
            if cursor.rowcount != 1:
                raise _lost_claim(claim)
            attempt = connection.execute(
                """
                UPDATE ingestion_attempt
                SET status = 'failed', error_msg = ?, finished_at = CURRENT_TIMESTAMP
                WHERE doc_key = ? AND generation = ? AND claim_token = ?
                  AND status IN ('claimed', 'staged')
                """,
                (error, claim.doc_key, claim.generation, claim.claim_token),
            )
            if attempt.rowcount != 1:
                raise _lost_claim(claim)

    def get_active_generations(self, collection: str | None = None) -> dict[str, int]:
        """返回查询可见的 ``doc_key -> generation`` 发布清单。"""
        sql = "SELECT doc_key, active_generation FROM document_state WHERE active_generation IS NOT NULL"
        parameters: tuple[str, ...] = ()
        if collection is not None:
            sql += " AND collection = ?"
            parameters = (collection,)
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return {str(row["doc_key"]): int(row["active_generation"]) for row in rows}

    def list_garbage_generations(self, doc_key: str) -> list[int]:
        """列出既非公开版本也非当前领取、因而可以延迟回收的 generations。"""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT attempt.generation
                FROM ingestion_attempt AS attempt
                JOIN document_state AS state ON state.doc_key = attempt.doc_key
                WHERE attempt.doc_key = ?
                  AND attempt.generation != COALESCE(state.active_generation, -1)
                  AND attempt.generation != COALESCE(state.claimed_generation, -1)
                ORDER BY attempt.generation
                """,
                (doc_key,),
            ).fetchall()
        return [int(row["generation"]) for row in rows]

    def list_processed(self, collection: str | None = None) -> list[JsonDict]:
        """按最近尝试列出摄取历史，并保留旧管理界面常用字段名。"""
        sql = """
            SELECT attempt.source_revision AS file_hash,
                   state.source_path AS file_path,
                   state.file_size,
                   state.collection,
                   CASE attempt.status
                       WHEN 'published' THEN 'success'
                       WHEN 'claimed' THEN 'processing'
                       WHEN 'staged' THEN 'processing'
                       ELSE attempt.status
                   END AS status,
                   COALESCE(attempt.finished_at, attempt.started_at) AS processed_at,
                   attempt.error_msg,
                   attempt.chunk_count,
                   attempt.lease_owner,
                   attempt.lease_expires_at,
                   attempt.doc_key,
                   attempt.generation,
                   attempt.status AS attempt_status
            FROM ingestion_attempt AS attempt
            JOIN document_state AS state ON state.doc_key = attempt.doc_key
        """
        parameters: tuple[str, ...] = ()
        if collection is not None:
            sql += " WHERE state.collection = ?"
            parameters = (collection,)
        sql += " ORDER BY processed_at DESC, state.source_path ASC, attempt.generation DESC"
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def remove_record(
        self,
        source_path_or_revision: str,
        collection: str | None = None,
    ) -> bool:
        """删除文档控制记录，使已清理的文档可以重新摄取。"""
        if not isinstance(source_path_or_revision, str) or not source_path_or_revision.strip():
            raise ValueError("file integrity error: document identity must be non-empty")

        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if collection is None:
                rows = connection.execute(
                    "SELECT DISTINCT doc_key FROM ingestion_attempt WHERE source_revision = ?",
                    (source_path_or_revision,),
                ).fetchall()
                doc_keys = [str(row["doc_key"]) for row in rows]
            else:
                doc_keys = [compute_doc_key(source_path_or_revision, collection)]

            removed = False
            for doc_key in doc_keys:
                connection.execute("DELETE FROM ingestion_attempt WHERE doc_key = ?", (doc_key,))
                cursor = connection.execute(
                    "DELETE FROM document_state WHERE doc_key = ?",
                    (doc_key,),
                )
                removed = removed or cursor.rowcount > 0
        return removed

    def _initialize(self) -> None:
        """创建分代控制表；保留旧 ingestion_history 表供人工迁移或审计。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_state (
                    doc_key TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    file_size INTEGER,
                    active_generation INTEGER,
                    active_revision TEXT,
                    last_generation INTEGER NOT NULL DEFAULT 0,
                    claimed_generation INTEGER,
                    claimed_revision TEXT,
                    claim_token TEXT,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ingestion_attempt (
                    doc_key TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    source_revision TEXT NOT NULL,
                    claim_token TEXT NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    status TEXT NOT NULL
                        CHECK(status IN ('claimed', 'staged', 'published', 'failed', 'fenced')),
                    error_msg TEXT,
                    chunk_count INTEGER,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT,
                    PRIMARY KEY (doc_key, generation),
                    FOREIGN KEY (doc_key) REFERENCES document_state(doc_key)
                );

                CREATE INDEX IF NOT EXISTS idx_document_state_collection
                    ON document_state(collection);
                CREATE INDEX IF NOT EXISTS idx_attempt_status
                    ON ingestion_attempt(status);
                CREATE INDEX IF NOT EXISTS idx_attempt_started_at
                    ON ingestion_attempt(started_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _validate_collection(collection: str) -> None:
    if (
        not isinstance(collection, str)
        or not collection.strip()
        or Path(collection).name != collection
        or collection in {".", ".."}
    ):
        raise ValueError("file integrity error: collection must be a simple name")


def _lost_claim(claim: ClaimHandle) -> RuntimeError:
    return RuntimeError(
        "file integrity error: claim is no longer current "
        f"for doc_key={claim.doc_key!r}, generation={claim.generation}"
    )


__all__ = [
    "FileIntegrityStore",
    "SQLiteIntegrityStore",
    "compute_doc_key",
    "compute_sha256",
    "normalize_source_path",
]
