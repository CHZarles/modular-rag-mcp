"""File integrity: SHA256 hashing for incremental ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1 << 16  # 64 KiB


def compute_sha256(source_path: str | Path) -> str:
    """Return the hex SHA256 digest of the file at ``source_path``.

    Raises:
        FileNotFoundError: If the path does not exist or is not a regular file.
    """
    p = Path(source_path)
    if not p.is_file():
        raise FileNotFoundError(f"not a regular file: {p}")
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            block = fh.read(CHUNK_SIZE)
            if not block:
                break
            h.update(block)
    return h.hexdigest()