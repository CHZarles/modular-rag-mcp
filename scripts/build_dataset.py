"""Run the project's splitter over the PostgreSQL corpus (plan §6 / §C5 eval).

Output: ``data/corpus/chunks.jsonl`` — one record per chunk with stable
``chunk_id`` (``{section_id}::{index}``), ``text``, ``section_id``,
``title``.

The script reads the corpus produced by ``scripts/fetch_postgres_corpus.py``
and uses :mod:`src.ingestion.splitter` so the chunks exercise the exact
path real ingestion uses. Re-running is idempotent — the chunks file is
regenerated from scratch each time.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CORPUS = PROJECT_ROOT / "data" / "corpus" / "corpus.jsonl"
DEFAULT_CHUNKS = PROJECT_ROOT / "data" / "corpus" / "chunks.jsonl"


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    section_id: str
    title: str
    text: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "chunk_id": self.chunk_id,
                "section_id": self.section_id,
                "title": self.title,
                "text": self.text,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    corpus_path = Path(args.corpus).expanduser()
    chunks_path = Path(args.output).expanduser()
    if not corpus_path.is_file():
        print(f"corpus file not found: {corpus_path}", file=sys.stderr)
        return 1

    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = list(build_chunks(_load_corpus(corpus_path)))
    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(chunk.to_jsonl() + "\n")

    print(
        f"wrote {len(chunks)} chunks to {chunks_path}",
        file=sys.stderr,
    )
    return 0


def build_chunks(
    corpus_records: Iterable[dict[str, Any]],
    *,
    splitter_factory: Any = None,
) -> Iterator[ChunkRecord]:
    """Chunk every corpus record through the project splitter."""
    splitter = splitter_factory() if splitter_factory is not None else _default_splitter()
    for record in corpus_records:
        section_id = record["section_id"]
        title = record["title"]
        text = record["text"]
        pieces = splitter.split_text(text)
        for index, piece in enumerate(pieces):
            yield ChunkRecord(
                chunk_id=f"{section_id}::{index:02d}",
                section_id=section_id,
                title=title,
                text=piece.strip(),
            )


def _default_splitter() -> Any:
    from src.libs.splitter.recursive_splitter import RecursiveSplitter

    # 320 chars ≈ 50-70 words; comfortable size for embedding + retrieval.
    # 32 char overlap keeps adjacent context across chunk boundaries.
    return RecursiveSplitter(
        {"provider": "recursive", "chunk_size": 320, "chunk_overlap": 32}
    )


def _load_corpus(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_dataset",
        description="Chunk the PostgreSQL corpus through the project splitter.",
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_CHUNKS))
    return parser


if __name__ == "__main__":  # pragma: no cover - module entry point
    from collections.abc import Sequence  # noqa: F401
    raise SystemExit(main())
