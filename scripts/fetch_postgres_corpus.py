"""Fetch and clean the PostgreSQL 16 documentation corpus used for RAG eval.

Source: postgresql.org/docs/16 (PostgreSQL License, BSD-style).
Output: data/corpus/corpus.jsonl — one record per page with stable
``section_id`` / ``title`` / ``text``.

This script is idempotent: re-running it overwrites the corpus file and
the bundled LICENSE attribution. Network I/O is minimal (one GET per
page); the script can be run offline by pointing ``--output`` at an
already-populated file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "corpus" / "corpus.jsonl"
DEFAULT_LICENSE = PROJECT_ROOT / "data" / "corpus" / "LICENSE"

# Curated subset: covers SQL basics, joins/aggregates, indexing, data types,
# and one advanced page each for transactions / windows / inheritance.
# Order is intentional — section_id doubles as a sort key when the test
# inspects per-section recall.
PAGES: tuple[tuple[str, str, str], ...] = (
    # (section_id, title, url)
    ("tutorial-arch", "Tutorial — Architectural Fundamentals",
     "https://www.postgresql.org/docs/16/tutorial-arch.html"),
    ("tutorial-createdb", "Tutorial — Creating a Database",
     "https://www.postgresql.org/docs/16/tutorial-createdb.html"),
    ("tutorial-accessdb", "Tutorial — Accessing a Database",
     "https://www.postgresql.org/docs/16/tutorial-accessdb.html"),
    ("tutorial-concepts", "Tutorial — Concepts",
     "https://www.postgresql.org/docs/16/tutorial-concepts.html"),
    ("tutorial-table", "Tutorial — Creating a New Table",
     "https://www.postgresql.org/docs/16/tutorial-table.html"),
    ("tutorial-populate", "Tutorial — Populating a Table With Rows",
     "https://www.postgresql.org/docs/16/tutorial-populate.html"),
    ("tutorial-select", "Tutorial — Querying a Table",
     "https://www.postgresql.org/docs/16/tutorial-select.html"),
    ("tutorial-join", "Tutorial — Joins Between Tables",
     "https://www.postgresql.org/docs/16/tutorial-join.html"),
    ("tutorial-agg", "Tutorial — Aggregate Functions",
     "https://www.postgresql.org/docs/16/tutorial-agg.html"),
    ("tutorial-update", "Tutorial — Updates",
     "https://www.postgresql.org/docs/16/tutorial-update.html"),
    ("tutorial-delete", "Tutorial — Deletions",
     "https://www.postgresql.org/docs/16/tutorial-delete.html"),
    ("tutorial-views", "Tutorial — Views",
     "https://www.postgresql.org/docs/16/tutorial-views.html"),
    ("tutorial-fk", "Tutorial — Foreign Keys",
     "https://www.postgresql.org/docs/16/tutorial-fk.html"),
    ("tutorial-transactions", "Tutorial — Transactions",
     "https://www.postgresql.org/docs/16/tutorial-transactions.html"),
    ("tutorial-window", "Tutorial — Window Functions",
     "https://www.postgresql.org/docs/16/tutorial-window.html"),
    ("tutorial-inheritance", "Tutorial — Inheritance",
     "https://www.postgresql.org/docs/16/tutorial-inheritance.html"),
    ("datatype-numeric", "Data Types — Numeric Types",
     "https://www.postgresql.org/docs/16/datatype-numeric.html"),
    ("datatype-character", "Data Types — Character Types",
     "https://www.postgresql.org/docs/16/datatype-character.html"),
    ("indexes-types", "Indexes — Index Types",
     "https://www.postgresql.org/docs/16/indexes-types.html"),
    ("indexes-multicolumn", "Indexes — Multicolumn Indexes",
     "https://www.postgresql.org/docs/16/indexes-multicolumn.html"),
    ("queries-overview", "Queries — Overview",
     "https://www.postgresql.org/docs/16/queries-overview.html"),
)


@dataclass(frozen=True)
class CorpusRecord:
    section_id: str
    title: str
    source_url: str
    text: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "section_id": self.section_id,
                "title": self.title,
                "source_url": self.source_url,
                "text": self.text,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.skip_license:
        _write_license(DEFAULT_LICENSE)

    records = list(fetch_pages(PAGES))
    if not records:
        print("no pages fetched — aborting", file=sys.stderr)
        return 1

    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.to_jsonl() + "\n")

    total = sum(len(record.text) for record in records)
    print(
        f"wrote {len(records)} records, {total} chars to {output_path}",
        file=sys.stderr,
    )
    return 0


def fetch_pages(
    pages: Iterable[tuple[str, str, str]],
    *,
    client_factory: Any = None,
) -> Iterator[CorpusRecord]:
    """Yield one :class:`CorpusRecord` per curated page.

    ``client_factory`` is the seam tests use to inject a stub HTTP client;
    production uses :class:`httpx.Client`.
    """
    factory = client_factory or (lambda: httpx.Client(timeout=15.0, follow_redirects=True))
    with factory() as client:
        for section_id, title, url in pages:
            response = client.get(url)
            response.raise_for_status()
            text = _extract_main(response.text)
            if len(text) < 200:
                # Likely a TOC-only stub; skip so a partial fetch still
                # produces a usable corpus.
                print(f"skip {section_id}: only {len(text)} chars", file=sys.stderr)
                continue
            yield CorpusRecord(
                section_id=section_id,
                title=title,
                source_url=url,
                text=text,
            )


# --- HTML stripping -------------------------------------------------------


_NAV_BLOCK_RE = re.compile(
    r"<div[^>]+class=\"nav(?:header|footer)\"[^>]*>.*?</div>", re.S
)
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)
_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES: dict[str, str] = {
    "&lt;": "<",
    "&gt;": ">",
    "&amp;": "&",
    "&quot;": "\"",
    "&#39;": "'",
    "&nbsp;": " ",
    "&rarr;": "→",
}


def _extract_main(html: str) -> str:
    """Strip the DocBook page chrome and return the chapter body as text.

    The PostgreSQL docs render every page in the same DocBook template:
    ``<div id="docContent">`` wraps a nested ``<div class="chapter">``
    that contains a leading ``navheader`` and a trailing ``navfooter``.
    The actual prose lives between them, starting at the first
    ``<h2 class="title">``.
    """
    start = html.find('id="docContent"')
    if start < 0:
        return ""
    nav1 = html.find('class="navheader"', start)
    title = html.find('<h2 class="title"', nav1 if nav1 > 0 else start)
    if title < 0:
        return ""
    end = html.find('class="navfooter"', title)
    if end < 0:
        end = len(html)
    body = html[title:end]
    body = _SCRIPT_RE.sub(" ", body)
    body = _NAV_BLOCK_RE.sub(" ", body)
    text = _TAGS_RE.sub(" ", body)
    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)
    return _WS_RE.sub(" ", text).strip()


def _write_license(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "PostgreSQL documentation is Copyright (c) 1996-2026 The PostgreSQL Global "
        "Development Group and is licensed under the PostgreSQL License, a BSD-style "
        "permissive licence.\n\n"
        "Full licence text: https://www.postgresql.org/about/licence/\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_postgres_corpus",
        description="Fetch and clean the PostgreSQL 16 docs corpus.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Where to write corpus.jsonl.",
    )
    parser.add_argument(
        "--skip-license",
        action="store_true",
        help="Do not (re)write the LICENSE attribution file.",
    )
    return parser


if __name__ == "__main__":  # pragma: no cover - module entry point
    from collections.abc import Sequence  # noqa: F401
    raise SystemExit(main())
