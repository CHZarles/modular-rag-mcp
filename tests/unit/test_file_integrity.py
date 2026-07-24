"""Tests for C2: file integrity (SHA256)."""

from __future__ import annotations

import hashlib

import pytest

from src.libs.loader.file_integrity import compute_sha256


def test_sha256_matches_hashlib_for_known_content(tmp_path) -> None:
    p = tmp_path / "blob.bin"
    data = b"hello world"
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert compute_sha256(p) == expected


def test_sha256_changes_with_content(tmp_path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert compute_sha256(a) != compute_sha256(b)


def test_sha256_handles_large_file(tmp_path) -> None:
    """Read the file in 64KiB chunks — verify with >1 chunk."""
    p = tmp_path / "big.bin"
    data = b"x" * (CHUNK := 200_000)  # >3 chunks
    p.write_bytes(data)
    assert compute_sha256(p) == hashlib.sha256(data).hexdigest()


def test_sha256_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        compute_sha256(tmp_path / "missing.bin")


def test_sha256_directory_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        compute_sha256(tmp_path)


def test_sha256_deterministic(tmp_path) -> None:
    p = tmp_path / "stable.bin"
    p.write_bytes(b"abc" * 100)
    assert compute_sha256(p) == compute_sha256(p)