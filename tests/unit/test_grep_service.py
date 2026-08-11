from __future__ import annotations

import hashlib
from pathlib import Path

from src.core.services.grep_service import GrepService
from src.core.types import ChunkRecord
from src.ingestion.storage.sqlite_grep_index import (
    GrepIndexRow,
    GrepQueryTimeoutError,
    SQLiteGrepIndex,
)


class _ActiveGenerations:
    def __init__(self, values: dict[str, int]) -> None:
        self.values = values
        self.calls = 0

    def get_active_generations(self, collection: str | None = None) -> dict[str, int]:
        self.calls += 1
        return dict(self.values)


def _record(
    text: str,
    *,
    doc_key: str,
    chunk_index: int,
    source_path: str,
    doc_type: str = "pdf",
) -> ChunkRecord:
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    suffix = hashlib.sha256(f"{chunk_index}\0{content_hash}".encode()).hexdigest()[:32]
    return ChunkRecord(
        id=f"{doc_key}:1:{suffix}",
        text=text,
        metadata={
            "collection": "docs",
            "doc_key": doc_key,
            "generation": 1,
            "source_path": source_path,
            "chunk_index": chunk_index,
            "page": chunk_index + 1,
            "doc_type": doc_type,
            "private": "not a service concern",
        },
        content_hash=content_hash,
    )


def test_service_normalizes_case_and_counts_non_overlapping_literal_matches(
    tmp_path: Path,
) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    doc_key = "d" * 64
    record = _record("ＡbC abc .* abcabc", doc_key=doc_key, chunk_index=0, source_path="/z.pdf")
    index.upsert([record])
    active = _ActiveGenerations({doc_key: 1})

    response = GrepService(index, active).search(
        pattern="abc",
        collection="docs",
        top_k=20,
        case_sensitive=False,
    )

    assert active.calls == 1
    assert response.timed_out is False
    assert response.truncated is False
    assert [(item.chunk_id, item.match_count) for item in response.matches] == [
        (record.id, 4)
    ]


def test_service_applies_python_literal_verification_and_stable_top_k(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    records = [
        _record("Needle in z", doc_key="a" * 64, chunk_index=0, source_path="/z.pdf"),
        _record("needle in a", doc_key="b" * 64, chunk_index=0, source_path="/a.pdf"),
        _record("needle in b", doc_key="c" * 64, chunk_index=0, source_path="/b.pdf"),
    ]
    for record in records:
        index.upsert([record])
    active = _ActiveGenerations({key * 64: 1 for key in "abc"})
    service = GrepService(index, active)

    sensitive = service.search(
        pattern="needle", collection="docs", top_k=20, case_sensitive=True
    )
    limited = service.search(
        pattern="needle", collection="docs", top_k=2, case_sensitive=False
    )

    assert [item.source_path for item in sensitive.matches] == ["/a.pdf", "/b.pdf"]
    assert [item.source_path for item in limited.matches] == ["/a.pdf", "/b.pdf"]
    assert limited.truncated is True


def test_service_filters_matches_by_document_type(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    pdf = _record(
        "shared needle",
        doc_key="a" * 64,
        chunk_index=0,
        source_path="/guide.pdf",
        doc_type="pdf",
    )
    csv = _record(
        "shared needle",
        doc_key="b" * 64,
        chunk_index=0,
        source_path="/data.csv",
        doc_type="csv",
    )
    index.upsert([pdf])
    index.upsert([csv])

    response = GrepService(
        index,
        _ActiveGenerations({"a" * 64: 1, "b" * 64: 1}),
    ).search(pattern="needle", collection="docs", file_type="csv")

    assert [match.chunk_id for match in response.matches] == [csv.id]


def test_service_enforces_one_and_twenty_result_boundaries_stably(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    records = [
        _record(
            f"needle {number}",
            doc_key=f"{number:064x}",
            chunk_index=0,
            source_path=f"/{number:02d}.txt",
        )
        for number in range(21)
    ]
    for record in records:
        index.upsert([record])
    service = GrepService(index, _ActiveGenerations({f"{number:064x}": 1 for number in range(21)}))

    one = service.search(pattern="needle", collection="docs", top_k=1)
    twenty = service.search(pattern="needle", collection="docs", top_k=20)
    repeated = service.search(pattern="needle", collection="docs", top_k=20)

    assert [match.chunk_id for match in one.matches] == [records[0].id]
    assert one.truncated is True
    assert [match.chunk_id for match in twenty.matches] == [record.id for record in records[:20]]
    assert [match.chunk_id for match in repeated.matches] == [match.chunk_id for match in twenty.matches]
    assert twenty.truncated is True


def test_service_uses_one_active_generation_snapshot_per_query(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    doc_key = "d" * 64
    first = _record("version one needle", doc_key=doc_key, chunk_index=0, source_path="/a.txt")
    second_text = "version two needle"
    second_hash = hashlib.sha256(second_text.encode()).hexdigest()
    second_suffix = hashlib.sha256(f"0\0{second_hash}".encode()).hexdigest()[:32]
    second = ChunkRecord(
        id=f"{doc_key}:2:{second_suffix}",
        text=second_text,
        metadata={**first.metadata, "generation": 2},
        content_hash=second_hash,
    )
    index.upsert([first])
    index.upsert([second])
    active = _ActiveGenerations({doc_key: 1})
    service = GrepService(index, active)

    before_publish = service.search(pattern="needle", collection="docs")
    active.values = {doc_key: 2}
    after_publish = service.search(pattern="needle", collection="docs")

    assert [match.text for match in before_publish.matches] == ["version one needle"]
    assert [match.text for match in after_publish.matches] == ["version two needle"]


def test_service_treats_quotes_spaces_and_fts_operators_as_literals(tmp_path: Path) -> None:
    index = SQLiteGrepIndex(tmp_path / "grep.db")
    index.initialize()
    doc_key = "d" * 64
    record = _record(
        'say "needle" AND [x]   done',
        doc_key=doc_key,
        chunk_index=0,
        source_path="/manual.pdf",
    )
    index.upsert([record])
    service = GrepService(index, _ActiveGenerations({doc_key: 1}))

    quoted = service.search(pattern='"needle"', collection="docs")
    spaces = service.search(pattern="   ", collection="docs")
    operator = service.search(pattern="AND [x]", collection="docs")

    assert [item.chunk_id for item in quoted.matches] == [record.id]
    assert [item.chunk_id for item in spaces.matches] == [record.id]
    assert [item.chunk_id for item in operator.matches] == [record.id]


def test_service_returns_verified_partial_results_on_timeout() -> None:
    class _TimedIndex:
        timeout_ms = 1000

        def iter_candidates(self, **_: object):  # type: ignore[no-untyped-def]
            yield GrepIndexRow("chunk-1", "needle", "/a.pdf", 1, {})
            raise GrepQueryTimeoutError("deadline")

    service = GrepService(  # type: ignore[arg-type]
        _TimedIndex(),
        _ActiveGenerations({"d" * 64: 1}),
    )

    response = service.search(pattern="needle", collection="docs")

    assert [item.chunk_id for item in response.matches] == ["chunk-1"]
    assert response.timed_out is True
    assert response.truncated is True
