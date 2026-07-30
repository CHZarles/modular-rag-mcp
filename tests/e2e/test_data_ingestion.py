"""离线摄取 CLI 的端到端测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from scripts.ingest import discover_pdf_files, main
from src.libs.embedding import EmbeddingFactory

pytestmark = pytest.mark.e2e


class DeterministicEmbedding:
    """用本地确定性向量替代在线 API，让 E2E 只验证摄取闭环。"""

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        return [
            [float(len(text)), float(sum(text.encode("utf-8")) % 997 + 1)]
            for text in texts
        ]


def test_cli_ingests_directory_skips_unchanged_and_supports_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    documents = tmp_path / "documents"
    first = _write_pdf(documents / "first.pdf", "First document about modular RAG.")
    _write_pdf(documents / "second.PDF", "Second document about sparse retrieval.")
    settings_path = _write_settings(tmp_path)
    EmbeddingFactory.register("e2e-deterministic", lambda config: DeterministicEmbedding())

    assert main(
        ["--path", str(documents), "--collection", "docs"],
        settings_path=settings_path,
    ) == 0
    first_output = capsys.readouterr()
    assert "成功 2，跳过 0，失败 0" in first_output.out
    assert (tmp_path / "data/db/chroma/chroma.sqlite3").is_file()
    assert (tmp_path / "data/db/bm25/index.pkl").is_file()
    assert (tmp_path / "data/db/ingestion_history.db").is_file()
    assert (tmp_path / "data/db/image_index.db").is_file()

    assert main(
        ["--path", str(documents), "--collection", "docs"],
        settings_path=settings_path,
    ) == 0
    second_output = capsys.readouterr()
    assert "成功 0，跳过 2，失败 0" in second_output.out
    assert "already_succeeded" in second_output.out

    assert main(
        ["--path", str(first), "--collection", "docs", "--force"],
        settings_path=settings_path,
    ) == 0
    force_output = capsys.readouterr()
    assert "成功 1，跳过 0，失败 0" in force_output.out

    with sqlite3.connect(tmp_path / "data/db/ingestion_history.db") as connection:
        generations = connection.execute(
            "SELECT last_generation FROM document_state ORDER BY source_path"
        ).fetchall()
    assert generations == [(2,), (1,)]


def test_discover_pdf_files_rejects_unsupported_or_empty_input(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not a PDF", encoding="utf-8")
    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()

    with pytest.raises(ValueError, match="只支持 PDF"):
        discover_pdf_files(text_file)
    with pytest.raises(ValueError, match="目录中没有 PDF"):
        discover_pdf_files(empty_directory)


def _write_pdf(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


def _write_settings(tmp_path: Path) -> Path:
    data_root = tmp_path / "data"
    settings = f"""\
knowledge_service:
  mode: local
llm:
  provider: openai
embedding:
  provider: e2e-deterministic
splitter:
  provider: recursive
  chunk_size: 300
  chunk_overlap: 30
ingestion:
  claim_lease_seconds: 60
  batch_size: 2
  storage:
    integrity_db_path: {data_root / 'db/ingestion_history.db'}
    bm25_path: {data_root / 'db/bm25'}
    image_db_path: {data_root / 'db/image_index.db'}
    image_root: {data_root / 'images'}
  chunk_refiner:
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    enabled: false
vector_store:
  backend: chroma
  persist_path: {data_root / 'db/chroma'}
  collection_name: e2e
  distance_metric: cosine
retrieval:
  sparse_backend: bm25
rerank:
  backend: none
evaluation:
  backends: [custom]
observability:
  enabled: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(settings, encoding="utf-8")
    return path
